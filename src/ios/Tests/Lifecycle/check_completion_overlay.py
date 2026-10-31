#!/usr/bin/env python3
"""Run the production overlay and result task in a real iOS Simulator host.

Requires Xcode, xcodegen and a booted simulator. --baseline removes phase
forwarding to reproduce the permanently loading result on the original path.
The generated fixture has no network, credentials, audio, or physical-device use.
"""
import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
APP = ROOT / "src/ios/DaythreadApp"
BUNDLE = "com.daythread.completion-lifecycle-test"


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def fixture(baseline):
    source = (APP / "CompletionNotifications.swift").read_text()
    start = source.index("struct CompletionSceneOverlay: UIViewRepresentable")
    overlay = source[start:source.index("/// BLE can wake", start)]
    if baseline:
        overlay = overlay.replace(".environment(\\.scenePhase, activity.phase)", "")
    result = (APP / "CompletionResultView.swift").read_text()
    start = result.index("        .task(id: model.archiveIdentity + event.id.uuidString")
    task = result[start:result.index("\n        .onDisappear", start)]
    task = task.replace("            if scenePhase", "            Report.phases.append(String(describing: scenePhase))\n            Report.identities.insert(identity.uuidString)\n            if scenePhase")
    return r'''
import SwiftUI
import UIKit
@Observable final class AppModel { var archiveIdentity = "test-archive" }
struct TestEvent: Identifiable { let id = UUID() }
@Observable final class CompletionNotifications {
    var banner: Bool?
    var opened: TestEvent? = TestEvent()
}
enum AppAppearance: String {
    case system
    var colorScheme: ColorScheme? { nil }
}
enum Theme { static let accent = Color.blue }
struct CompletionBanner: View { var body: some View { Text("Banner") } }
@MainActor enum Report {
    static var phases: [String] = []
    static var identities: Set<String> = []
    static var appearances = 0
    static var requests = 0
    static func save() {
        let value: [String:Any] = ["phases":phases, "identities":Array(identities), "appearances":appearances,
                                   "requests":requests, "application_active":UIApplication.shared.applicationState == .active]
        let data = try! JSONSerialization.data(withJSONObject:value, options:[.sortedKeys])
        let file = FileManager.default.urls(for:.documentDirectory,in:.userDomainMask)[0].appendingPathComponent("report.json")
        try! data.write(to:file)
    }
}
struct CompletionResultView: View {
    let event: TestEvent
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var identity = UUID()
    @State private var loading = true
    var body: some View {
        Text(loading ? "Opening saved result…" : "Question list loaded")
        .onAppear { Report.appearances += 1 }
__TASK__
    }
    private func load() async { Report.requests += 1; loading = false }
}
__OVERLAY__
struct MainView: View {
    @Environment(\.scenePhase) private var phase
    @State private var model = AppModel()
    @State private var notifications = CompletionNotifications()
    @State private var overridePhase: ScenePhase?
    var body: some View {
        Text("Main scene")
            .background(CompletionSceneOverlay(model:model, notifications:notifications)
                .environment(\.scenePhase, overridePhase ?? phase))
            .onAppear {
                DispatchQueue.main.asyncAfter(deadline:.now()+1) { overridePhase = .inactive }
                DispatchQueue.main.asyncAfter(deadline:.now()+2) { overridePhase = .active }
                DispatchQueue.main.asyncAfter(deadline:.now()+3) { overridePhase = .background }
                DispatchQueue.main.asyncAfter(deadline:.now()+4) { overridePhase = .active }
                DispatchQueue.main.asyncAfter(deadline:.now()+5) { Report.save() }
            }
    }
}
@main struct TestApp: App { var body: some Scene { WindowGroup { MainView() } } }
'''.replace("__TASK__", task).replace("__OVERLAY__", overlay)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulator", help="Booted Simulator UUID (defaults to first booted iPhone)")
    parser.add_argument("--baseline", action="store_true", help="Remove forwarding; expected assertion failure")
    args = parser.parse_args()
    devices = json.loads(subprocess.check_output(["xcrun", "simctl", "list", "devices", "booted", "--json"], text=True))
    candidates = [item["udid"] for group in devices["devices"].values() for item in group if item["state"] == "Booted"]
    simulator = args.simulator or (candidates[0] if candidates else None)
    if not simulator:
        parser.error("Boot an iOS Simulator first; this test never uses a physical device.")
    if simulator not in candidates:
        parser.error("--simulator must identify a booted Simulator.")
    with tempfile.TemporaryDirectory(prefix="daythread-completion-overlay-") as directory:
        directory = Path(directory)
        (directory / "App.swift").write_text(fixture(args.baseline))
        (directory / "project.yml").write_text('''name: CompletionLifecycle
options:
  deploymentTarget:
    iOS: "17.0"
targets:
  CompletionLifecycle:
    type: application
    platform: iOS
    sources: [App.swift]
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: com.daythread.completion-lifecycle-test
        GENERATE_INFOPLIST_FILE: YES
        INFOPLIST_KEY_UIApplicationSceneManifest_Generation: YES
        INFOPLIST_KEY_UILaunchScreen_Generation: YES
        CODE_SIGNING_ALLOWED: NO
        SWIFT_VERSION: "5.0"
''')
        log = Path("/tmp/daythread-completion-overlay-build.log")
        with log.open("w") as output:
            run("xcodegen", "generate", "--spec", str(directory / "project.yml"), stdout=output, stderr=subprocess.STDOUT)
            run("xcodebuild", "-project", str(directory / "CompletionLifecycle.xcodeproj"), "-scheme", "CompletionLifecycle",
                "-sdk", "iphonesimulator", "-destination", "id=" + simulator, "-derivedDataPath", str(directory / "build"),
                "build", stdout=output, stderr=subprocess.STDOUT)
        subprocess.run(["xcrun", "simctl", "terminate", simulator, BUNDLE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        run("xcrun", "simctl", "install", simulator, str(directory / "build/Build/Products/Debug-iphonesimulator/CompletionLifecycle.app"))
        container = Path(subprocess.check_output(["xcrun", "simctl", "get_app_container", simulator, BUNDLE, "data"], text=True).strip())
        report = container / "Documents/report.json"
        report.unlink(missing_ok=True)
        run("xcrun", "simctl", "launch", simulator, BUNDLE, stdout=subprocess.DEVNULL)
        for _ in range(100):
            if report.exists():
                break
            time.sleep(.1)
        value = json.loads(report.read_text())
        print(json.dumps(value, sort_keys=True))
        assert value["application_active"], "Test must run in the foreground"
        assert value["requests"] >= 3, "Opening saved result is stuck: initial/return-to-active job fetch did not run"
        assert {"active", "inactive", "background"}.issubset(value["phases"]), "Hosted sheet did not receive scene phase transitions"
        assert value["appearances"] == 1 and len(value["identities"]) == 1, "Phase updates reset the presented result sheet"
        print("PASS: initial fetch, inactive/background pause, active reload, and stable presented sheet")


if __name__ == "__main__":
    main()
