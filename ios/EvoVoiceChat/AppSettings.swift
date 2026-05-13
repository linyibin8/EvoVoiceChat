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
        BackendPreset(id: "public", name: "公网", url: "https://evovoice.evowit.com"),
        BackendPreset(id: "tailscale", name: "Tailscale", url: "http://100.64.0.2:30190"),
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

    var parsedSourceDomains: [String] {
        sourceDomains
            .split { $0 == "," || $0 == "\n" || $0 == " " || $0 == "，" }
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
            .filter { !$0.isEmpty }
    }
}
