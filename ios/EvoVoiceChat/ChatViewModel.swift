import Foundation

@MainActor
final class ChatViewModel: ObservableObject {
    @Published var messages: [ChatMessage] = [
        ChatMessage(role: .assistant, content: "你好，我是 Evo Voice。")
    ]
    @Published var inputText: String = ""
    @Published var liveTranscript: String = ""
    @Published var isListening: Bool = false
    @Published var isSending: Bool = false
    @Published var isSynthesizing: Bool = false
    @Published var isPlaying: Bool = false
    @Published var synthesisElapsed: Double = 0
    @Published var lastTTSMetrics = TTSMetrics()
    @Published var lastTimings: [String: Double] = [:]
    @Published var errorMessage: String?
    @Published var healthText: String = "未连接"

    private let api = APIClient()
    private let speech = SpeechRecognitionService()
    private let player = AudioPlaybackService()
    private var synthesisTicker: Task<Void, Never>?
    private var playbackTask: Task<Void, Never>?
    private var silenceTask: Task<Void, Never>?
    private weak var activeSettings: AppSettings?

    init() {
        speech.onTranscript = { [weak self] transcript, _ in
            Task { @MainActor in
                self?.liveTranscript = transcript
                self?.scheduleHandsFreeSend(for: transcript)
            }
        }
        speech.onStateChange = { [weak self] recording in
            Task { @MainActor in
                self?.isListening = recording
            }
        }
        player.onStateChange = { [weak self] playing in
            Task { @MainActor in
                self?.isPlaying = playing
            }
        }
    }

    func checkHealth(settings: AppSettings) {
        Task {
            do {
                let health = try await api.health(settings: settings)
                let model = health.chat?.model ?? "unknown"
                healthText = health.ok ? "已连接 \(model)" : "后端异常"
            } catch {
                healthText = "连接失败"
            }
        }
    }

    func sendTapped(settings: AppSettings) {
        let prompt = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !prompt.isEmpty else { return }
        inputText = ""
        send(prompt: prompt, settings: settings, speakAnswer: true)
    }

    func startListening(settings: AppSettings) {
        guard !isListening else { return }
        activeSettings = settings
        liveTranscript = ""
        errorMessage = nil
        player.stop()
        Task {
            do {
                try await speech.start()
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }

    func stopListeningAndSend(settings: AppSettings) {
        silenceTask?.cancel()
        speech.stop()
        let prompt = liveTranscript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !prompt.isEmpty else { return }
        liveTranscript = ""
        send(prompt: prompt, settings: settings, speakAnswer: true)
    }

    func interrupt() {
        silenceTask?.cancel()
        speech.stop()
        player.stop()
        synthesisTicker?.cancel()
        playbackTask?.cancel()
        isSynthesizing = false
        isSending = false
    }

    private func send(prompt: String, settings: AppSettings, speakAnswer: Bool) {
        guard !isSending else { return }
        errorMessage = nil
        isSending = true
        let userMessage = ChatMessage(role: .user, content: prompt)
        messages.append(userMessage)

        Task {
            do {
                let response = try await api.sendChat(messages: messages, prompt: prompt, settings: settings)
                lastTimings = response.timings_ms
                if let warnings = response.warnings, !warnings.isEmpty {
                    errorMessage = warnings.joined(separator: "；")
                }
                let assistant = ChatMessage(role: .assistant, content: response.assistant_text, sources: response.search_results)
                messages.append(assistant)
                isSending = false
                if speakAnswer {
                    playbackTask?.cancel()
                    playbackTask = Task { [weak self, weak settings] in
                        guard let self, let settings else { return }
                        await self.synthesizeAndPlay(response.assistant_text, settings: settings)
                    }
                }
            } catch {
                isSending = false
                errorMessage = error.localizedDescription
                messages.append(ChatMessage(role: .assistant, content: "这次请求失败：\(error.localizedDescription)"))
            }
        }
    }

    private func scheduleHandsFreeSend(for transcript: String) {
        guard let settings = activeSettings, settings.handsFreeMode, isListening else { return }
        let snapshot = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !snapshot.isEmpty else { return }
        silenceTask?.cancel()
        silenceTask = Task { [weak self, weak settings] in
            try? await Task.sleep(nanoseconds: 1_400_000_000)
            await MainActor.run {
                guard let self, let settings else { return }
                let current = self.liveTranscript.trimmingCharacters(in: .whitespacesAndNewlines)
                if self.isListening, current == snapshot {
                    self.stopListeningAndSend(settings: settings)
                }
            }
        }
    }

    private func synthesizeAndPlay(_ text: String, settings: AppSettings) async {
        let cleanText = Self.voiceText(from: text)
        guard !cleanText.isEmpty else { return }
        let chunks = Self.ttsChunks(from: cleanText)
        guard !chunks.isEmpty else { return }

        isSynthesizing = true
        synthesisElapsed = 0
        lastTTSMetrics = TTSMetrics(segmentCount: chunks.count)
        let started = Date()
        var firstChunkMs: Double = 0
        var totalSynthesisMs: Double = 0
        var combinedAudioDuration: Double = 0
        var combinedBytes = 0
        var completedSegments = 0

        synthesisTicker?.cancel()
        synthesisTicker = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 100_000_000)
                await MainActor.run {
                    guard let self, self.isSynthesizing else { return }
                    self.synthesisElapsed = Date().timeIntervalSince(started)
                }
            }
        }

        do {
            var tasks: [Int: Task<TTSChunkResult, Error>] = [:]
            var nextIndex = 0
            let maxInflight = 3

            func scheduleNext() {
                guard nextIndex < chunks.count else { return }
                let index = nextIndex
                let chunk = chunks[index]
                nextIndex += 1
                tasks[index] = Task { [api, settings] in
                    let result = try await api.synthesize(text: chunk, settings: settings)
                    return TTSChunkResult(index: index, result: result, finishedAt: Date())
                }
            }

            for _ in 0..<min(maxInflight, chunks.count) {
                scheduleNext()
            }
            defer {
                tasks.values.forEach { $0.cancel() }
            }

            for index in chunks.indices {
                try Task.checkCancellation()
                guard let task = tasks.removeValue(forKey: index) else { continue }
                let chunkResult = try await task.value
                scheduleNext()

                let chunkMs = chunkResult.finishedAt.timeIntervalSince(started) * 1000
                if firstChunkMs == 0 {
                    firstChunkMs = chunkMs
                }
                totalSynthesisMs = max(totalSynthesisMs, chunkMs)
                combinedAudioDuration += chunkResult.result.metrics.audioDurationSeconds
                combinedBytes += chunkResult.result.metrics.bytes
                completedSegments += 1
                lastTTSMetrics = Self.combinedMetrics(
                    started: started,
                    text: cleanText,
                    segmentCount: chunks.count,
                    firstChunkMs: firstChunkMs,
                    totalSynthesisMs: totalSynthesisMs,
                    audioDurationSeconds: combinedAudioDuration,
                    bytes: combinedBytes
                )
                isSynthesizing = !tasks.isEmpty
                try await player.playAndWait(fileURL: chunkResult.result.fileURL)
            }

            synthesisTicker?.cancel()
            isSynthesizing = false
            lastTTSMetrics = Self.combinedMetrics(
                started: started,
                text: cleanText,
                segmentCount: chunks.count,
                firstChunkMs: firstChunkMs,
                totalSynthesisMs: totalSynthesisMs,
                audioDurationSeconds: combinedAudioDuration,
                bytes: combinedBytes
            )
            if settings.handsFreeMode {
                startListening(settings: settings)
            }
        } catch is CancellationError {
            synthesisTicker?.cancel()
            isSynthesizing = false
        } catch {
            synthesisTicker?.cancel()
            isSynthesizing = false
            errorMessage = "语音合成失败：\(error.localizedDescription)"
        }
    }

    private static func voiceText(from text: String) -> String {
        text
            .replacingOccurrences(of: #"\[[0-9]+\]"#, with: "", options: .regularExpression)
            .replacingOccurrences(of: #"https?://\S+"#, with: "", options: .regularExpression)
            .replacingOccurrences(of: "\n", with: " ")
            .replacingOccurrences(of: "  ", with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func ttsChunks(from text: String) -> [String] {
        let normalized = text
            .replacingOccurrences(of: "  ", with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty else { return [] }

        let delimiters = CharacterSet(charactersIn: "。！？!?；;")
        var chunks: [String] = []
        var current = ""
        for scalar in normalized.unicodeScalars {
            current.unicodeScalars.append(scalar)
            if delimiters.contains(scalar), current.count >= 10 {
                chunks.append(current.trimmingCharacters(in: .whitespacesAndNewlines))
                current = ""
            } else if current.count >= 70 {
                chunks.append(current.trimmingCharacters(in: .whitespacesAndNewlines))
                current = ""
            }
        }
        let tail = current.trimmingCharacters(in: .whitespacesAndNewlines)
        if !tail.isEmpty {
            chunks.append(tail)
        }
        return chunks.filter { !$0.isEmpty }
    }

    private static func combinedMetrics(
        started: Date,
        text: String,
        segmentCount: Int,
        firstChunkMs: Double,
        totalSynthesisMs: Double,
        audioDurationSeconds: Double,
        bytes: Int
    ) -> TTSMetrics {
        let elapsedSeconds = Date().timeIntervalSince(started)
        let synthSeconds = max(totalSynthesisMs / 1000, 0.001)
        let rtf = audioDurationSeconds > 0 ? synthSeconds / audioDurationSeconds : 0
        let charsPerSecond = synthSeconds > 0 ? Double(text.count) / synthSeconds : 0
        return TTSMetrics(
            elapsedSeconds: elapsedSeconds,
            latencyMs: totalSynthesisMs,
            audioDurationSeconds: audioDurationSeconds,
            rtf: rtf,
            charsPerSecond: charsPerSecond,
            bytes: bytes,
            segmentCount: segmentCount,
            firstChunkMs: firstChunkMs,
            totalSynthesisMs: totalSynthesisMs
        )
    }
}

private struct TTSChunkResult {
    let index: Int
    let result: TTSResult
    let finishedAt: Date
}
