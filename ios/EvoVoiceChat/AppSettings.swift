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
    static let defaultBackendURL = "https://evovoice.evowit.com"
    static let defaultTTSVoice = "voxcpm:auto"
    private static let legacyVoiceDesignPrompt = "A warm young Chinese woman, natural conversational assistant voice, clear pronunciation, slightly fast pace"
    static let backendPresets: [BackendPreset] = [
        BackendPreset(id: "public-domain", name: "公网域名", url: defaultBackendURL),
    ]
    private static let legacyLocalBackendURLs: Set<String> = [
        "http://192.168.0.11:30190",
        "http://127.0.0.1:30190",
        "http://100.64.0.2:30190",
        "http://100.64.0.3:30190",
    ]

    @AppStorage("backendURL") var backendURL: String = AppSettings.defaultBackendURL {
        willSet { objectWillChange.send() }
    }
    @AppStorage("sourceDomains") var sourceDomains: String = "news.qq.com,finance.sina.com.cn,36kr.com,wallstreetcn.com,reuters.com" {
        willSet { objectWillChange.send() }
    }
    @AppStorage("searchEnabled") var searchEnabled: Bool = true {
        willSet { objectWillChange.send() }
    }
    @AppStorage("remoteServerDefaultsApplied") private var remoteServerDefaultsApplied: Bool = false
    @AppStorage("webSearchDefaultEnabledApplied") private var webSearchDefaultEnabledApplied: Bool = false
    @AppStorage("handsFreeMode") var handsFreeMode: Bool = false {
        willSet { objectWillChange.send() }
    }
    @AppStorage("preferOnDeviceSpeech") var preferOnDeviceSpeech: Bool = true {
        willSet { objectWillChange.send() }
    }
    @AppStorage("ttsVoice") var ttsVoice: String = AppSettings.defaultTTSVoice {
        willSet { objectWillChange.send() }
    }
    @AppStorage("referenceTTSVoiceApplied") private var referenceTTSVoiceApplied: Bool = false
    @AppStorage("maxSearchResults") var maxSearchResults: Int = 6 {
        willSet { objectWillChange.send() }
    }

    init() {
        migrateLocalBackendToRemoteDomain()
        migrateDefaultTTSVoice()
        enableWebSearchByDefaultIfNeeded()
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

    private func migrateLocalBackendToRemoteDomain() {
        guard !remoteServerDefaultsApplied else { return }
        let current = backendURL.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        if current.isEmpty || AppSettings.legacyLocalBackendURLs.contains(current) {
            backendURL = AppSettings.defaultBackendURL
        }
        remoteServerDefaultsApplied = true
    }

    private func migrateDefaultTTSVoice() {
        guard !referenceTTSVoiceApplied else { return }
        let current = ttsVoice.trimmingCharacters(in: .whitespacesAndNewlines)
        if current.isEmpty || current == "default" || current == AppSettings.legacyVoiceDesignPrompt {
            ttsVoice = AppSettings.defaultTTSVoice
        }
        referenceTTSVoiceApplied = true
    }

    private func enableWebSearchByDefaultIfNeeded() {
        guard !webSearchDefaultEnabledApplied else { return }
        searchEnabled = true
        webSearchDefaultEnabledApplied = true
    }

    var parsedSourceDomains: [String] {
        sourceDomains
            .split { $0 == "," || $0 == "\n" || $0 == " " || $0 == "，" }
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
            .filter { !$0.isEmpty }
    }
}
