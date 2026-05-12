import Foundation

enum ChatRole: String, Codable {
    case user
    case assistant
    case system
}

struct ChatMessage: Identifiable, Equatable {
    let id: UUID
    var role: ChatRole
    var content: String
    var createdAt: Date
    var sources: [SearchResult]

    init(id: UUID = UUID(), role: ChatRole, content: String, createdAt: Date = Date(), sources: [SearchResult] = []) {
        self.id = id
        self.role = role
        self.content = content
        self.createdAt = createdAt
        self.sources = sources
    }
}

struct APIChatMessage: Codable {
    let role: String
    let content: String
}

struct SearchOptions: Codable {
    let enabled: Bool
    let query: String?
    let source_domains: [String]
    let max_results: Int
}

struct ChatRequest: Codable {
    let messages: [APIChatMessage]
    let search: SearchOptions
}

struct SearchResult: Codable, Identifiable, Equatable {
    var id: String { link }
    let title: String
    let link: String
    let source: String?
    let published_at: String?
    let snippet: String?
}

struct ChatResponse: Codable {
    let assistant_text: String
    let search_results: [SearchResult]
    let timings_ms: [String: Double]
    let model: String
}

struct TTSMetrics: Equatable {
    var elapsedSeconds: Double = 0
    var latencyMs: Double = 0
    var audioDurationSeconds: Double = 0
    var rtf: Double = 0
    var charsPerSecond: Double = 0
    var bytes: Int = 0

    var hasFinalMetrics: Bool {
        latencyMs > 0 || audioDurationSeconds > 0 || rtf > 0
    }
}

struct TTSResult {
    let fileURL: URL
    let metrics: TTSMetrics
}

struct HealthResponse: Codable {
    struct ProviderStatus: Codable {
        let base_url: String?
        let model: String?
        let configured: Bool?
    }

    let ok: Bool
    let chat: ProviderStatus?
    let tts: ProviderStatus?
    let stt: ProviderStatus?
}
