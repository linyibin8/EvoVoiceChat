import SwiftUI
import UIKit

@main
struct EvoVoiceChatApp: App {
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var settings = AppSettings()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(settings)
                .onAppear {
                    setIdleTimerDisabled(true)
                }
                .onChange(of: scenePhase) { _, newPhase in
                    setIdleTimerDisabled(newPhase == .active)
                }
        }
    }

    private func setIdleTimerDisabled(_ isDisabled: Bool) {
        UIApplication.shared.isIdleTimerDisabled = isDisabled
    }
}
