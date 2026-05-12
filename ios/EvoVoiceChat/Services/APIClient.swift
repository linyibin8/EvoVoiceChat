import Foundation

enum APIClientError: LocalizedError {
    case invalidBackendURL
    case invalidResponse
    case serverError(Int, String)

    var errorDescription: String? {
        switch self {
        case .invalidBackendURL:
            return "后端地址无效"
        case .invalidResponse:
            return "后端响应格式异常"
        case .serverError(let code, let message):
            return "后端错误 \(code)：\(message)"
        }
    }
}

@MainActor
final class APIClient {
    private let session: URLSession
    private let decoder = JSONDecoder()
    private let encoder = JSONEncoder()

    init(session: URLSession = APIClient.makeSession()) {
        self.session = session
    }

    func health(settings: AppSettings) async throws -> HealthResponse {
        let url = try endpoint(settings: settings, path: "/health")
        let (data, response) = try await session.data(from: url)
        try validate(response: response, data: data)
        return try decoder.decode(HealthResponse.self, from: data)
    }

    func sendChat(messages: [ChatMessage], prompt: String, settings: AppSettings) async throws -> ChatResponse {
        let url = try endpoint(settings: settings, path: "/api/chat")
        let history = messages.suffix(12).map { APIChatMessage(role: $0.role.rawValue, content: $0.content) }
        let requestBody = ChatRequest(
            messages: history,
            search: SearchOptions(
                enabled: settings.searchEnabled,
                query: prompt,
                source_domains: settings.parsedSourceDomains,
                max_results: settings.maxSearchResults
            )
        )
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try encoder.encode(requestBody)
        request.timeoutInterval = 180
        let (data, response) = try await data(for: request)
        try validate(response: response, data: data)
        return try decoder.decode(ChatResponse.self, from: data)
    }

    func synthesize(text: String, settings: AppSettings) async throws -> TTSResult {
        let url = try endpoint(settings: settings, path: "/api/tts")
        let body = ["text": text, "voice": settings.ttsVoice]
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        request.timeoutInterval = 120
        let (data, response) = try await data(for: request)
        try validate(response: response, data: data)
        guard let http = response as? HTTPURLResponse else {
            throw APIClientError.invalidResponse
        }
        let fileURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("evovoice-\(UUID().uuidString)")
            .appendingPathExtension("wav")
        try data.write(to: fileURL, options: .atomic)
        let metrics = TTSMetrics(
            latencyMs: doubleHeader(http, "X-Evo-TTS-Latency-Ms"),
            audioDurationSeconds: doubleHeader(http, "X-Evo-Audio-Duration-S"),
            rtf: doubleHeader(http, "X-Evo-TTS-RTF"),
            charsPerSecond: doubleHeader(http, "X-Evo-TTS-Chars-Per-Second"),
            bytes: Int(doubleHeader(http, "X-Evo-TTS-Bytes"))
        )
        return TTSResult(fileURL: fileURL, metrics: metrics)
    }

    private func endpoint(settings: AppSettings, path: String) throws -> URL {
        guard let base = settings.normalizedBackendURL else {
            throw APIClientError.invalidBackendURL
        }
        return base.appendingPathComponent(path.trimmingCharacters(in: CharacterSet(charactersIn: "/")))
    }

    nonisolated private static func makeSession() -> URLSession {
        let configuration = URLSessionConfiguration.default
        configuration.waitsForConnectivity = true
        configuration.timeoutIntervalForRequest = 180
        configuration.timeoutIntervalForResource = 240
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: configuration)
    }

    private func data(for request: URLRequest, retries: Int = 2) async throws -> (Data, URLResponse) {
        var lastError: Error?
        for attempt in 0...retries {
            do {
                return try await session.data(for: request)
            } catch let error as URLError where error.isRetryableNetworkLoss && attempt < retries {
                lastError = error
                try await Task.sleep(nanoseconds: UInt64(600_000_000) * UInt64(attempt + 1))
            } catch {
                throw error
            }
        }
        throw lastError ?? APIClientError.invalidResponse
    }

    private func validate(response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else {
            throw APIClientError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "unknown"
            throw APIClientError.serverError(http.statusCode, message)
        }
    }

    private func doubleHeader(_ response: HTTPURLResponse, _ name: String) -> Double {
        guard let value = response.value(forHTTPHeaderField: name) else { return 0 }
        return Double(value) ?? 0
    }
}

private extension URLError {
    var isRetryableNetworkLoss: Bool {
        switch code {
        case .networkConnectionLost,
             .timedOut,
             .cannotConnectToHost,
             .cannotFindHost,
             .dnsLookupFailed,
             .notConnectedToInternet,
             .secureConnectionFailed:
            return true
        default:
            return false
        }
    }
}
