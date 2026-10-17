import SwiftUI
import OmiCore

struct ManagedAccountView: View {
    @Environment(AppModel.self) private var model
    @State private var email = ""
    @State private var password = ""
    @State private var error: String?
    @State private var working = false
    private var session: ManagedAccountSession { model.managedAccount }
    private var credits: String {
        (Double(session.account?.credits.available_units ?? 0) / 1000).formatted(.number.precision(.fractionLength(0...3)))
    }

    var body: some View {
        Form {
            Section {
                Text(L10n.text("Sign in for managed hosting and model credits, or keep using your own backend."))
                    .foregroundStyle(.secondary)
            }
            if session.signedIn {
                Section(L10n.text("Your Daythread account")) {
                    if let account = session.account {
                        LabeledContent(L10n.text("Email"), value: account.email)
                        LabeledContent(session.hostingVerified ? L10n.text("Available credits") : L10n.text("Last known credit balance"), value: credits)
                        LabeledContent(L10n.text("Managed hosting"), value: account.hosting_active ? L10n.text("Active") : L10n.text("Not active"))
                        if account.hosted_until > 0 {
                            LabeledContent(L10n.text("Paid through"), value: Date(timeIntervalSince1970: account.hosted_until).formatted(date: .abbreviated, time: .omitted))
                        }
                        if !session.hostingVerified {
                            Text(L10n.text("Account details are from your last connection. Refresh to confirm hosting and credits.")).font(.caption).foregroundStyle(.secondary)
                        }
                        if account.credits.reserved_units > 0 {
                            Text(L10n.text("Some credits are reserved for processing.")).font(.caption).foregroundStyle(.secondary)
                        }
                        if !account.hosting_active {
                            Text(L10n.text("Your credits can be used with your own backend. Activate hosting on your account page to use your hosted archive in this app."))
                                .font(.callout).foregroundStyle(.secondary)
                        }
                    } else { Text(L10n.text("Account details are not available offline.")).foregroundStyle(.secondary) }
                    Button(L10n.text("Refresh account")) { Task { await session.refresh() } }
                        .disabled(session.loading || working).accessibilityIdentifier("managed.refresh")
                }
                Section {
                    if let url = session.origin?.url {
                        LabeledContent(L10n.text("Managed service"), value: url.host ?? url.absoluteString)
                    }
                    Text(L10n.text("The managed service address is fixed by this app. Your own backend endpoints are kept separately."))
                        .font(.caption).foregroundStyle(.secondary)
                    if let url = session.accountURL {
                        Link(L10n.text("Manage hosting & credits"), destination: url)
                    }
                }
                Section {
                    Button(L10n.text("Sign out and use own backend"), role: .destructive) { useOwnBackend() }
                        .disabled(working).accessibilityIdentifier("managed.signout")
                } footer: {
                    Text(L10n.text("Queued recordings stay with their original account and backend. They are never reassigned when you sign out."))
                }
            } else {
                Section {
                    TextField(L10n.text("Email"), text: $email)
                        .textContentType(.username).keyboardType(.emailAddress)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                        .accessibilityIdentifier("managed.email")
                    SecureField(L10n.text("Password"), text: $password)
                        .textContentType(.password).accessibilityIdentifier("managed.password")
                    Button(L10n.text("Sign in")) { signIn() }
                        .disabled(working || session.loading || session.origin == nil || email.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || password.isEmpty)
                        .accessibilityIdentifier("managed.signin")
                    if let url = session.accountURL {
                        Link(L10n.text("Create account"), destination: url)
                    } else {
                        Text(L10n.text("Managed sign-in is not configured in this app build.")).font(.caption).foregroundStyle(.secondary)
                    }
                } header: { Text(L10n.text("Sign in to Daythread")) } footer: {
                    Text(L10n.text("Signing in connects this app to our managed hosting. No backend URL is needed."))
                }
                Section {
                    if model.usesManagedHosting {
                        Button(L10n.text("Use own backend")) { useOwnBackend() }
                            .disabled(working).accessibilityIdentifier("managed.useOwnBackend")
                    } else {
                        NavigationLink(L10n.text("Own backend endpoints")) { BackendEndpointsView() }
                    }
                }
            }
            if working || session.loading { ProgressView(L10n.text("Connecting…")) }
            if let message = error ?? session.error { Section { ErrorNote(message: message) } }
        }
        .omiScreen().navigationTitle(L10n.text("Account & connection")).navigationBarTitleDisplayMode(.inline)
        .task { await session.refresh() }
    }

    private func signIn() {
        working = true; error = nil
        Task {
            defer { working = false; password = "" }
            do {
                model.flush()
                guard model.store?.isRecording != true else {
                    throw BackendFailure(message: L10n.source("Finish saving the current recording before changing connections."))
                }
                try await session.signIn(email: email, password: password)
            } catch { self.error = L10n.error(error) }
        }
    }
    private func useOwnBackend() {
        working = true; error = nil
        Task {
            defer { working = false }
            do { try await model.useOwnBackend() }
            catch { self.error = L10n.error(error) }
        }
    }
}
