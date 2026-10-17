import SwiftUI
import UserNotifications
import CryptoKit
import OmiCore

/// Owns delivery across APNs and the foreground feed. System presentation is
/// suppressed by the delegate even if a push raced with opening the app.
@MainActor @Observable
final class CompletionNotifications {
    static let shared = CompletionNotifications()
    private(set) var inbox = CompletionInbox()
    var banner: CompletionNotification?
    var opened: CompletionNotification?
    private(set) var permission: UNAuthorizationStatus = .notDetermined
    private(set) var pushConfigured = false
    private(set) var status: String?
    private(set) var active = false
    @ObservationIgnored private var model: AppModel?
    @ObservationIgnored private var client: BackendClient?
    @ObservationIgnored private var scope = ""
    @ObservationIgnored private var installation = ""
    @ObservationIgnored private var generation = UUID()
    @ObservationIgnored private var previewRevision = UUID()
    @ObservationIgnored private var rehydrate = true
    @ObservationIgnored private var operation: Task<Void, Never>?
    @ObservationIgnored private var registeredToken: String?
    @ObservationIgnored private var pendingPush: ([AnyHashable: Any], Bool)?
    @ObservationIgnored private var token = UserDefaults.standard.string(forKey: "notifications.apnsToken")

    var items: [CompletionNotification] { inbox.items }
    var authorized: Bool { permission == .authorized || permission == .provisional || permission == .ephemeral }

    func configure(model: AppModel) {
        self.model = model
        if scope == model.archiveIdentity && client?.profileID == model.selectedProfileID {
            client?.router.replace(model.backendConfiguration)
            return
        }
        let oldClient = client, oldInstallation = installation
        let previous = operation
        previous?.cancel()
        generation = UUID()
        let current = generation
        scope = model.archiveIdentity
        // A profile/archive switch must not retarget cleanup requests through the
        // AppModel's mutable router to the newly selected archive.
        client = model.hasSelectedProfile ? try? BackendClient(router: BackendEndpointRouter(configuration: model.backendConfiguration), profileID: model.selectedProfileID) : nil
        let key = storageKey("installation")
        installation = UserDefaults.standard.string(forKey: key) ?? UUID().uuidString.lowercased()
        UserDefaults.standard.set(installation, forKey: key)
        inbox = UserDefaults.standard.data(forKey: storageKey("inbox")).flatMap { try? JSONDecoder().decode(CompletionInbox.self, from: $0) } ?? CompletionInbox()
        banner = nil; opened = nil; registeredToken = nil; status = nil; pushConfigured = false
        rehydrate = true; previewRevision = UUID()
        operation = Task {
            await previous?.value
            if let oldClient, !oldInstallation.isEmpty {
                _ = try? await oldClient.request("v1/notifications/devices/" + oldInstallation, method: "DELETE")
            }
            guard generation == current, !Task.isCancelled else { return }
            if let pendingPush { self.pendingPush = nil; receivePush(pendingPush.0, open: pendingPush.1) }
            if active { await foregroundLoop(generation: current) }
        }
    }

    func setActive(_ value: Bool) {
        guard active != value else { return }
        active = value
        let previous = operation
        previous?.cancel()
        generation = UUID()
        let current = generation
        let backgroundTask = value ? UIBackgroundTaskIdentifier.invalid : UIApplication.shared.beginBackgroundTask(withName: "Notification presence")
        operation = Task {
            defer { if backgroundTask != .invalid { UIApplication.shared.endBackgroundTask(backgroundTask) } }
            await previous?.value
            guard generation == current, !Task.isCancelled else { return }
            if value { await foregroundLoop(generation: current) }
            else if let client, registeredToken != nil {
                _ = try? await client.request("v1/notifications/devices/" + installation + "/presence", method: "POST", body: ["active": false])
            }
        }
    }

    func requestPermission() async {
        do {
            _ = try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])
            await refreshPermission()
            if authorized { UIApplication.shared.registerForRemoteNotifications() }
        } catch { status = error.localizedDescription }
    }

    func receivedToken(_ data: Data) {
        token = data.map { String(format: "%02x", $0) }.joined()
        UserDefaults.standard.set(token, forKey: "notifications.apnsToken")
        // The foreground loop retries registration until the backend accepts it.
        registeredToken = nil
    }

    func registrationFailed(_ error: Error) { status = "Push registration: " + error.localizedDescription }

    func receivePush(_ payload: [AnyHashable: Any], open: Bool) {
        guard client != nil else { pendingPush = (payload, open); return }
        guard CompletionInbox.acceptsPush(profile: payload["profile_id"] as? String ?? "", installation: payload["device_id"] as? String ?? "", currentProfile: client?.profileID ?? "", currentInstallation: installation),
              let raw = payload["notification"], JSONSerialization.isValidJSONObject(raw),
              let data = try? JSONSerialization.data(withJSONObject: raw),
              let event = try? JSONDecoder().decode(CompletionNotification.self, from: data) else { return }
        let new = inbox.receive(event)
        save()
        if open { opened = event; banner = nil }
        else if active && new { banner = event }
        // Ack immediately even when an APNs event was already consumed by polling.
        if let client {
            let id = installation
            Task { _ = try? await client.request("v1/notifications/ack", method: "POST", body: ["installation_id": id, "ids": [event.id.uuidString.lowercased()]]) }
        }
    }

    func open(_ item: CompletionNotification) { banner = nil; opened = item }
    func clearPreviews() { inbox.clearPreviews(); banner = nil; opened = nil; rehydrate = true; previewRevision = UUID() }

    private func foregroundLoop(generation current: UUID) async {
        await refreshPermission()
        guard current == generation, !Task.isCancelled else { return }
        if let pendingPush { self.pendingPush = nil; receivePush(pendingPush.0, open: pendingPush.1) }
        if authorized { UIApplication.shared.registerForRemoteNotifications() }
        while active && current == generation && !Task.isCancelled {
            do {
                guard let client else { return }
                if authorized, let token {
                    if registeredToken != token {
                        _ = try await client.request("v1/notifications/devices/" + installation, method: "PUT", body: ["token": token, "environment": Self.pushEnvironment, "active": true])
                        // Keep the successful registration even if backgrounding raced
                        // with the response, so the serialized background task clears it.
                        registeredToken = token
                    } else {
                        _ = try await client.request("v1/notifications/devices/" + installation + "/presence", method: "POST", body: ["active": true])
                    }
                }
                guard current == generation, !Task.isCancelled else { return }
                let readiness = try await client.request("v1/notifications/status")
                guard current == generation, !Task.isCancelled else { return }
                pushConfigured = readiness["push_configured"] == .bool(true)
                try await readFeed(client: client, generation: current)
                if current == generation { status = nil }
            } catch {
                if current == generation, !Task.isCancelled { status = error.localizedDescription; registeredToken = nil }
            }
            do { try await Task.sleep(for: .seconds(10)) } catch { return }
        }
    }

    private struct Page: Decodable { let notifications: [CompletionNotification]; let cursor: Int }
    private func readFeed(client: BackendClient, generation current: UUID) async throws {
        var after = rehydrate ? 0 : inbox.cursor
        let revision = previewRevision
        repeat {
            let page = try await client.decoded("v1/notifications", query: ["after": String(after)], as: Page.self)
            guard active, current == generation, revision == previewRevision, !Task.isCancelled else { return }
            var latest: CompletionNotification?
            for event in page.notifications { if inbox.receive(event) { latest = event } }
            if let latest { banner = latest }
            // A failed acknowledgement leaves the page cursor intact for retry.
            if registeredToken != nil && !page.notifications.isEmpty {
                _ = try await client.request("v1/notifications/ack", method: "POST", body: ["installation_id": installation, "ids": page.notifications.map { $0.id.uuidString.lowercased() }])
            }
            guard active, current == generation, revision == previewRevision, !Task.isCancelled else { return }
            let advanced = page.cursor > after
            after = page.cursor
            inbox.advanceCursor(to: page.cursor)
            save()
            if page.notifications.count < 100 || !advanced { rehydrate = false; return }
        } while active && current == generation && !Task.isCancelled
    }

    private func refreshPermission() async {
        permission = await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
        if !authorized, let client {
            _ = try? await client.request("v1/notifications/devices/" + installation, method: "DELETE")
            registeredToken = nil
        }
    }

    private func storageKey(_ part: String) -> String {
        let hash = SHA256.hash(data: Data(scope.utf8)).map { String(format: "%02x", $0) }.joined()
        return "notifications.\(part).\(hash)"
    }
    private func save() { if let data = try? JSONEncoder().encode(inbox) { UserDefaults.standard.set(data, forKey: storageKey("inbox")) } }

    private static var pushEnvironment: String {
        // Both this Info.plist value and aps-environment use APS_ENVIRONMENT in
        // project.yml. Distribution provisioning can override the signed value;
        // prefer its entitlement when the embedded profile is available.
        if let url = Bundle.main.url(forResource: "embedded", withExtension: "mobileprovision"),
           let data = try? Data(contentsOf: url), let contents = String(data: data, encoding: .isoLatin1),
           let start = contents.range(of: "<?xml"), let end = contents.range(of: "</plist>", range: start.lowerBound..<contents.endIndex),
           let plistData = String(contents[start.lowerBound..<end.upperBound]).data(using: .isoLatin1),
           let profile = try? PropertyListSerialization.propertyList(from: plistData, format: nil) as? [String: Any],
           let entitlements = profile["Entitlements"] as? [String: Any], let environment = entitlements["aps-environment"] as? String {
            return environment == "development" ? "sandbox" : "production"
        }
        return Bundle.main.object(forInfoDictionaryKey: "APNSEnvironment") as? String == "development" ? "sandbox" : "production"
    }
}

final class CompletionNotificationDelegate: NSObject, UNUserNotificationCenterDelegate {
    static let shared = CompletionNotificationDelegate()
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification, withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        // Never show a system banner, sound, list entry, or badge in the foreground.
        completionHandler([])
        Task { @MainActor in CompletionNotifications.shared.receivePush(notification.request.content.userInfo, open: false) }
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse, withCompletionHandler completionHandler: @escaping () -> Void) {
        Task { @MainActor in
            CompletionNotifications.shared.receivePush(response.notification.request.content.userInfo, open: true)
            completionHandler()
        }
    }
}

struct CompletionBanner: View {
    @Environment(CompletionNotifications.self) private var notifications
    var body: some View {
        if let event = notifications.banner {
            HStack(alignment: .top, spacing: 12) {
                Button { notifications.open(event) } label: {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(event.title).font(.headline)
                        Text(event.body).font(.subheadline).lineLimit(3)
                        Text("Open result").font(.caption.weight(.semibold)).foregroundStyle(Theme.accent)
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }.buttonStyle(.plain).accessibilityIdentifier("notification.open")
                Button { notifications.banner = nil } label: {
                    Image(systemName: "xmark").frame(width: 32, height: 32)
                }.buttonStyle(.plain).accessibilityLabel("Dismiss notification")
            }.padding(16).background(Theme.surface, in: RoundedRectangle(cornerRadius: 18))
                .overlay(RoundedRectangle(cornerRadius: 18).stroke(Theme.accent.opacity(0.25)))
                .shadow(color: .black.opacity(0.12), radius: 12, y: 4).padding(.horizontal).padding(.top, 8)
        }
    }
}

struct CompletionNotificationSettings: View {
    @Environment(CompletionNotifications.self) private var notifications
    var body: some View {
        Section {
            if notifications.authorized {
                Label("Push notifications allowed", systemImage: "bell.badge")
            } else if notifications.permission == .denied {
                Text("Notifications are disabled in iPhone Settings.")
            } else {
                Button("Enable push notifications") { Task { await notifications.requestPermission() } }
                    .accessibilityIdentifier("settings.enableNotifications")
            }
            Link("Notification settings on this iPhone", destination: URL(string: UIApplication.openNotificationSettingsURLString)!)
            if notifications.authorized && !notifications.pushConfigured {
                Text("Home-core needs Apple push credentials before it can send notifications while the app is closed.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if let status = notifications.status { Text(status).font(.caption).foregroundStyle(.secondary) }
        } header: { Text("Notifications") } footer: {
            Text("Get a notification when a rewind, recap, translation or weekly review is ready while Daythread is closed. While you use the app, results appear here as in-app notifications. Push previews include the result’s title, date or language; control lock-screen previews in iPhone Settings.")
        }
    }
}

/// A scene-level overlay keeps completion notices visible over source and other
/// sheets. Only the banner (or its opened result sheet) receives touches.
struct CompletionSceneOverlay: UIViewRepresentable {
    let model: AppModel
    let notifications: CompletionNotifications

    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeUIView(context: Context) -> Anchor {
        let anchor = Anchor()
        anchor.isUserInteractionEnabled = false
        anchor.onScene = { [weak coordinator = context.coordinator] scene in
            coordinator?.install(scene: scene, model: model, notifications: notifications)
        }
        return anchor
    }
    func updateUIView(_ uiView: Anchor, context: Context) {}
    static func dismantleUIView(_ uiView: Anchor, coordinator: Coordinator) { coordinator.window?.isHidden = true }

    final class Anchor: UIView {
        var onScene: ((UIWindowScene) -> Void)?
        override func didMoveToWindow() { super.didMoveToWindow(); if let scene = window?.windowScene { onScene?(scene) } }
    }
    final class Coordinator {
        var window: BannerWindow?
        func install(scene: UIWindowScene, model: AppModel, notifications: CompletionNotifications) {
            guard window == nil else { return }
            let overlay = BannerWindow(windowScene: scene)
            overlay.windowLevel = .normal + 1
            let root = OverlayContent(frameChanged: { [weak overlay] frame in overlay?.bannerFrame = frame })
                .environment(model).environment(notifications)
            let controller = UIHostingController(rootView: root)
            controller.view.backgroundColor = .clear
            overlay.rootViewController = controller
            overlay.backgroundColor = .clear
            overlay.isHidden = false
            window = overlay
        }
    }
    final class BannerWindow: UIWindow {
        var bannerFrame = CGRect.zero
        override func point(inside point: CGPoint, with event: UIEvent?) -> Bool {
            rootViewController?.presentedViewController != nil || bannerFrame.contains(point)
        }
        override func hitTest(_ point: CGPoint, with event: UIEvent?) -> UIView? {
            if rootViewController?.presentedViewController != nil { return super.hitTest(point, with: event) }
            guard bannerFrame.contains(point) else { return nil }
            return super.hitTest(point, with: event)
        }
    }
    private struct BannerFrame: PreferenceKey {
        static var defaultValue = CGRect.zero
        static func reduce(value: inout CGRect, nextValue: () -> CGRect) {
            let next = nextValue()
            if !next.isEmpty { value = next }
        }
    }
    private struct OverlayContent: View {
        let frameChanged: (CGRect) -> Void
        @Environment(AppModel.self) private var model
        @Environment(CompletionNotifications.self) private var notifications
        @AppStorage("appearancePreference") private var appearance = AppAppearance.system
        var body: some View {
            ZStack(alignment: .top) {
                Color.clear.allowsHitTesting(false)
                if notifications.banner != nil {
                    CompletionBanner().background(GeometryReader { proxy in
                        Color.clear.preference(key: BannerFrame.self, value: proxy.frame(in: .global))
                    })
                }
            }
            .onPreferenceChange(BannerFrame.self, perform: frameChanged)
            .preferredColorScheme(appearance.colorScheme)
            .tint(Theme.accent)
            .sheet(item: Binding(get: { notifications.opened }, set: { notifications.opened = $0 })) { event in
                CompletionResultView(event: event).id(model.archiveIdentity + event.id.uuidString)
            }
        }
    }
}
