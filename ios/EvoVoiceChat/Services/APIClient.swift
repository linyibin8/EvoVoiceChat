import Foundation

enum APIClientError: LocalizedError {
    case invalidBackendURL
    case invalidResponse
    case serverError(Int, String)
    case streamError(String)

    var errorDescription: String? {
        switch self {
        case .invalidBackendURL:
            return "后端地址无效"
        case .invalidResponse:
            return "后端响应格式异常"
        case .serverError(let code, let message):
            return "后端错误 \(code)：\(message)"
        case .streamError(let message):
            return message
        }
    }
}

extension Error {
    var isTransientNetworkFailure: Bool {
        let code: URLError.Code
        if let urlError = self as? URLError {
            code = urlError.code
        } else {
            let nsError = self as NSError
            guard nsError.domain == NSURLErrorDomain else { return false }
            code = URLError.Code(rawValue: nsError.code)
        }

        switch code {
        case .networkConnectionLost,
             .timedOut,
             .cannotConnectToHost,
             .cannotFindHost,
             .dnsLookupFailed,
             .notConnectedToInternet,
             .cannotLoadFromNetwork,
             .internationalRoamingOff,
             .callIsActive,
             .dataNotAllowed:
            return true
        default:
            return false
        }
    }

    var isRecoverableChatStreamFailure: Bool {
        if isTransientNetworkFailure {
            return true
        }

        if let apiError = self as? APIClientError {
            switch apiError {
            case .serverError(let code, _):
                return [408, 429, 500, 502, 503, 504].contains(code)
            case .streamError(let message):
                let lowercased = message.lowercased()
                let transientFragments = [
                    "peer closed connection",
                    "incomplete chunked",
                    "server disconnected",
                    "connection reset",
                    "connection closed",
                    "timed out",
                    "timeout",
                    "temporarily unavailable",
                    "too many requests",
                    "bad gateway",
                    "service unavailable",
                    "gateway timeout",
                    "remote protocol"
                ]
                return transientFragments.contains { lowercased.contains($0) }
            default:
                return false
            }
        }

        return false
    }
}

enum ChatStreamEvent {
    case metadata(searchResults: [SearchResult], timings: [String: Double], model: String?, warnings: [String])
    case delta(String)
    case done(timings: [String: Double], model: String?)
}

private struct ChatStreamPayload: Codable {
    let text: String?
    let search_results: [SearchResult]?
    let timings_ms: [String: Double]?
    let model: String?
    let warnings: [String]?
    let message: String?
}

@MainActor
final class APIClient {
    private let session: URLSession
    private let decoder = JSONDecoder()
    private let encoder = JSONEncoder()

    init(session: URLSession = .shared) {
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
        let requestBody = makeChatRequest(messages: messages, prompt: prompt, settings: settings)
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try encoder.encode(requestBody)
        request.timeoutInterval = 180
        let (data, response) = try await dataWithTransientRetry(for: request)
        try validate(response: response, data: data)
        return try decoder.decode(ChatResponse.self, from: data)
    }

    func streamChat(
        messages: [ChatMessage],
        prompt: String,
        settings: AppSettings,
        onEvent: (ChatStreamEvent) -> Void
    ) async throws {
        let url = try endpoint(settings: settings, path: "/api/chat/stream")
        let requestBody = makeChatRequest(messages: messages, prompt: prompt, settings: settings)
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        request.httpBody = try encoder.encode(requestBody)
        request.timeoutInterval = 180

        let (bytes, response) = try await session.bytes(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIClientError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            throw APIClientError.serverError(http.statusCode, HTTPURLResponse.localizedString(forStatusCode: http.statusCode))
        }

        var eventName = "message"
        var dataLines: [String] = []
        func flushEvent() throws {
            try handleStreamEvent(name: eventName, dataLines: dataLines, onEvent: onEvent)
            eventName = "message"
            dataLines.removeAll(keepingCapacity: true)
        }

        for try await line in bytes.lines {
            if line.hasPrefix("event:") {
                if !dataLines.isEmpty {
                    try flushEvent()
                }
                eventName = String(line.dropFirst(6)).trimmingCharacters(in: .whitespaces)
            } else if line.hasPrefix("data:") {
                dataLines.append(String(line.dropFirst(5)).trimmingCharacters(in: .whitespaces))
            } else if line.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                try flushEvent()
            }
        }
        if !dataLines.isEmpty {
            try flushEvent()
        }
    }

    func synthesize(text: String, settings: AppSettings) async throws -> TTSResult {
        let url = try endpoint(settings: settings, path: "/api/tts")
        let body = ["text": text, "voice": settings.ttsVoice]
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        request.timeoutInterval = 120
        let (data, response) = try await dataWithTransientRetry(for: request)
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

    private func dataWithTransientRetry(for request: URLRequest, attempts: Int = 2) async throws -> (Data, URLResponse) {
        var lastError: Error?
        for attempt in 0..<attempts {
            do {
                return try await session.data(for: request)
            } catch {
                lastError = error
                if !error.isTransientNetworkFailure || attempt == attempts - 1 {
                    throw error
                }
                try await Task.sleep(nanoseconds: UInt64(400_000_000 * (attempt + 1)))
            }
        }
        throw lastError ?? APIClientError.invalidResponse
    }

    private func makeChatRequest(messages: [ChatMessage], prompt: String, settings: AppSettings) -> ChatRequest {
        let history = messages.suffix(12).map { APIChatMessage(role: $0.role.rawValue, content: $0.content) }
        return ChatRequest(
            messages: history,
            search: SearchOptions(
                enabled: settings.searchEnabled,
                query: prompt,
                source_domains: settings.parsedSourceDomains,
                max_results: settings.maxSearchResults
            )
        )
    }

    private func endpoint(settings: AppSettings, path: String) throws -> URL {
        guard let base = settings.normalizedBackendURL else {
            throw APIClientError.invalidBackendURL
        }
        return base.appendingPathComponent(path.trimmingCharacters(in: CharacterSet(charactersIn: "/")))
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

    private func handleStreamEvent(
        name: String,
        dataLines: [String],
        onEvent: (ChatStreamEvent) -> Void
    ) throws {
        guard !dataLines.isEmpty else { return }
        let data = dataLines.joined(separator: "\n")
        guard let payloadData = data.data(using: .utf8) else {
            throw APIClientError.invalidResponse
        }
        let payload = try decoder.decode(ChatStreamPayload.self, from: payloadData)
        switch name {
        case "metadata":
            onEvent(.metadata(
                searchResults: payload.search_results ?? [],
                timings: payload.timings_ms ?? [:],
                model: payload.model,
                warnings: payload.warnings ?? []
            ))
        case "delta":
            if let text = payload.text, !text.isEmpty {
                onEvent(.delta(text))
            }
        case "done":
            onEvent(.done(timings: payload.timings_ms ?? [:], model: payload.model))
        case "error":
            throw APIClientError.streamError(payload.message ?? "流式回答失败")
        default:
            break
        }
    }
}
