import Foundation
import SwiftUI

@MainActor
final class AppSettings: ObservableObject {
    @AppStorage("backendURL") var backendURL: String = "https://evovoice.evowit.com" {
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
    @AppStorage("ttsVoice") var ttsVoice: String = "default" {
        willSet { objectWillChange.send() }
    }
    @AppStorage("maxSearchResults") var maxSearchResults: Int = 6 {
        willSet { objectWillChange.send() }
    }

    var normalizedBackendURL: URL? {
        URL(string: backendURL.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/")))
    }

    var parsedSourceDomains: [String] {
        sourceDomains
            .split { $0 == "," || $0 == "\n" || $0 == " " || $0 == "，" }
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
            .filter { !$0.isEmpty }
    }
}
