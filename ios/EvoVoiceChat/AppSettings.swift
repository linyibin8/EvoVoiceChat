import Foundation
import SwiftUI

struct BackendPreset: Identifiable, Hashable {
    let id: String
    let name: String
    let url: String
}

@MainActor
final class AppSettings: ObservableObject {
    static let customBackendPresetID = "custom"
    static let defaultBackendURL = "http://192.168.0.11:30190"
    static let backendPresets: [BackendPreset] = [
        BackendPreset(id: "local-lan", name: "本机局域网", url: "http://192.168.0.11:30190"),
        BackendPreset(id: "local-loopback", name: "本机模拟器", url: "http://127.0.0.1:30190"),
    ]
    private static let legacyRemoteBackendURLs: Set<String> = [
        "https://evovoice.evowit.com",
        "http://100.64.0.2:30190",
        "http://100.64.0.3:30190",
    ]

    @AppStorage("backendURL") var backendURL: String = AppSettings.defaultBackendURL {
        willSet { objectWillChange.send() }
    }
    @AppStorage("sourceDomains") var sourceDomains: String = "news.qq.com,finance.sina.com.cn,36kr.com,wallstreetcn.com,reuters.com" {
        willSet { objectWillChange.send() }
    }
    @AppStorage("searchEnabled") var searchEnabled: Bool = false {
        willSet { objectWillChange.send() }
    }
    @AppStorage("localLANTestModeApplied") private var localLANTestModeApplied: Bool = false
    @AppStorage("handsFreeMode") var handsFreeMode: Bool = false {
        willSet { objectWillChange.send() }
    }
    @AppStorage("preferOnDeviceSpeech") var preferOnDeviceSpeech: Bool = true {
        willSet { objectWillChange.send() }
    }
    @AppStorage("ttsVoice") var ttsVoice: String = "default" {
        willSet { objectWillChange.send() }
    }
    @AppStorage("maxSearchResults") var maxSearchResults: Int = 6 {
        willSet { objectWillChange.send() }
    }

    init() {
        migrateRemoteBackendToLocalLAN()
        applyLocalLANTestDefaultsIfNeeded()
    }

    var normalizedBackendURL: URL? {
        URL(string: backendURL.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/")))
    }

    var matchingBackendPresetID: String {
        let current = normalizedBackendURL?.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        return AppSettings.backendPresets.first { preset in
            preset.url.trimmingCharacters(in: CharacterSet(charactersIn: "/")) == current
        }?.id ?? AppSettings.customBackendPresetID
    }

    func applyBackendPreset(_ presetID: String) {
        guard let preset = AppSettings.backendPresets.first(where: { $0.id == presetID }) else { return }
        backendURL = preset.url
    }

    private func migrateRemoteBackendToLocalLAN() {
        let current = backendURL.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        if current.isEmpty || AppSettings.legacyRemoteBackendURLs.contains(current) {
            backendURL = AppSettings.defaultBackendURL
        }
    }

    private func applyLocalLANTestDefaultsIfNeeded() {
        guard !localLANTestModeApplied else { return }
        searchEnabled = false
        localLANTestModeApplied = true
    }

    var parsedSourceDomains: [String] {
        sourceDomains
            .split { $0 == "," || $0 == "\n" || $0 == " " || $0 == "，" }
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
            .filter { !$0.isEmpty }
    }
}
