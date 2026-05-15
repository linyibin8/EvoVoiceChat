import Foundation

@MainActor
final class ChatViewModel: ObservableObject {
    @Published var messages: [ChatMessage] = [
        ChatMessage(role: .assistant, content: "你好，我是 Evo Voice。可以打字，也可以按住麦克风直接说。")
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
    private var responseTask: Task<Void, Never>?
    private var ttsWorkerTask: Task<Void, Never>?
    private var synthesisTicker: Task<Void, Never>?
    private var silenceTask: Task<Void, Never>?
    private var queuedSpeechSegments: [String] = []
    private var pendingSpeechText = ""
    private weak var activeSettings: AppSettings?

    private let hardSpeechBreaks: Set<Character> = ["。", "！", "？", "!", "?", "\n"]
    private let softSpeechBreaks: Set<Character> = ["，", ",", "；", ";", "：", ":"]

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
                try await speech.start(preferOnDevice: settings.preferOnDeviceSpeech)
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
        responseTask?.cancel()
        responseTask = nil
        ttsWorkerTask?.cancel()
        ttsWorkerTask = nil
        queuedSpeechSegments.removeAll()
        pendingSpeechText = ""
        silenceTask?.cancel()
        speech.stop()
        player.stop()
        synthesisTicker?.cancel()
        synthesisTicker = nil
        isSynthesizing = false
        isSending = false
    }

    private func send(prompt: String, settings: AppSettings, speakAnswer: Bool) {
        guard !isSending else { return }
        responseTask?.cancel()
        ttsWorkerTask?.cancel()
        queuedSpeechSegments.removeAll()
        pendingSpeechText = ""
        player.stop()
        synthesisTicker?.cancel()
        errorMessage = nil
        isSending = true
        isSynthesizing = false
        lastTimings = [:]
        lastTTSMetrics = TTSMetrics()

        let userMessage = ChatMessage(role: .user, content: prompt)
        messages.append(userMessage)
        let requestMessages = messages
        let assistantID = UUID()
        messages.append(ChatMessage(id: assistantID, role: .assistant, content: ""))

        responseTask = Task { [weak self, settings] in
            guard let self else { return }
            defer {
                if !Task.isCancelled {
                    self.responseTask = nil
                }
            }
            do {
                try await self.api.streamChat(messages: requestMessages, prompt: prompt, settings: settings) { event in
                    self.handleStreamEvent(event, assistantID: assistantID, settings: settings, speakAnswer: speakAnswer)
                }
                self.finishStreamingResponse(assistantID: assistantID, settings: settings, speakAnswer: speakAnswer)
            } catch is CancellationError {
                return
            } catch {
                await self.fallbackToNonStreamingChat(
                    messages: requestMessages,
                    prompt: prompt,
                    settings: settings,
                    assistantID: assistantID,
                    speakAnswer: speakAnswer,
                    originalError: error
                )
            }
        }
    }

    private func fallbackToNonStreamingChat(
        messages requestMessages: [ChatMessage],
        prompt: String,
        settings: AppSettings,
        assistantID: UUID,
        speakAnswer: Bool,
        originalError: Error
    ) async {
        guard originalError.isRecoverableChatStreamFailure else {
            isSending = false
            errorMessage = originalError.localizedDescription
            replaceEmptyAssistant(assistantID: assistantID, with: "这次请求失败：\(originalError.localizedDescription)")
            return
        }

        errorMessage = "流式连接中断，已自动切换普通请求。"
        queuedSpeechSegments.removeAll()
        pendingSpeechText = ""
        player.stop()

        do {
            let response = try await api.sendChat(messages: requestMessages, prompt: prompt, settings: settings)
            setAssistantContent(assistantID: assistantID, text: response.assistant_text)
            updateAssistantSources(assistantID: assistantID, sources: response.search_results)
            mergeTimings(response.timings_ms)
            if let warning = response.warnings?.first {
                errorMessage = warning
            } else {
                errorMessage = nil
            }
            isSending = false
            guard speakAnswer else { return }
            pendingSpeechText = response.assistant_text
            enqueueReadySpeechSegments(settings: settings, force: true)
            startTTSWorkerIfNeeded(settings: settings)
        } catch {
            isSending = false
            errorMessage = error.localizedDescription
            replaceEmptyAssistant(assistantID: assistantID, with: "这次请求失败：\(error.localizedDescription)")
        }
    }

    private func handleStreamEvent(
        _ event: ChatStreamEvent,
        assistantID: UUID,
        settings: AppSettings,
        speakAnswer: Bool
    ) {
        switch event {
        case .metadata(let searchResults, let timings, _, let warnings):
            mergeTimings(timings)
            updateAssistantSources(assistantID: assistantID, sources: searchResults)
            if let warning = warnings.first {
                errorMessage = warning
            }
        case .delta(let text):
            appendToAssistant(assistantID: assistantID, text: text)
            guard speakAnswer else { return }
            pendingSpeechText += text
            enqueueReadySpeechSegments(settings: settings, force: false)
        case .done(let timings, _):
            mergeTimings(timings)
            isSending = false
        }
    }

    private func finishStreamingResponse(assistantID: UUID, settings: AppSettings, speakAnswer: Bool) {
        isSending = false
        removeEmptyAssistantIfNeeded(assistantID: assistantID)
        guard speakAnswer else { return }
        enqueueReadySpeechSegments(settings: settings, force: true)
        startTTSWorkerIfNeeded(settings: settings)
    }

    private func scheduleHandsFreeSend(for transcript: String) {
        guard let settings = activeSettings, settings.handsFreeMode, isListening else { return }
        let snapshot = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !snapshot.isEmpty else { return }
        silenceTask?.cancel()
        silenceTask = Task { [weak self, weak settings] in
            try? await Task.sleep(nanoseconds: 850_000_000)
            await MainActor.run {
                guard let self, let settings else { return }
                let current = self.liveTranscript.trimmingCharacters(in: .whitespacesAndNewlines)
                if self.isListening, current == snapshot {
                    self.stopListeningAndSend(settings: settings)
                }
            }
        }
    }

    private func appendToAssistant(assistantID: UUID, text: String) {
        guard let index = messages.firstIndex(where: { $0.id == assistantID }) else { return }
        messages[index].content += text
    }

    private func setAssistantContent(assistantID: UUID, text: String) {
        guard let index = messages.firstIndex(where: { $0.id == assistantID }) else {
            messages.append(ChatMessage(role: .assistant, content: text))
            return
        }
        messages[index].content = text
    }

    private func updateAssistantSources(assistantID: UUID, sources: [SearchResult]) {
        guard let index = messages.firstIndex(where: { $0.id == assistantID }) else { return }
        messages[index].sources = sources
    }

    private func replaceEmptyAssistant(assistantID: UUID, with text: String) {
        guard let index = messages.firstIndex(where: { $0.id == assistantID }) else {
            messages.append(ChatMessage(role: .assistant, content: text))
            return
        }
        if messages[index].content.isEmpty {
            messages[index].content = text
        } else {
            messages.append(ChatMessage(role: .assistant, content: text))
        }
    }

    private func removeEmptyAssistantIfNeeded(assistantID: UUID) {
        guard let index = messages.firstIndex(where: { $0.id == assistantID }) else { return }
        if messages[index].content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
           messages[index].sources.isEmpty {
            messages.remove(at: index)
        }
    }

    private func mergeTimings(_ timings: [String: Double]) {
        for (key, value) in timings {
            lastTimings[key] = value
        }
    }

    private func enqueueReadySpeechSegments(settings: AppSettings, force: Bool) {
        let segments = drainSpeechSegments(force: force)
        guard !segments.isEmpty else { return }
        queuedSpeechSegments.append(contentsOf: segments)
        startTTSWorkerIfNeeded(settings: settings)
    }

    private func drainSpeechSegments(force: Bool) -> [String] {
        var segments: [String] = []
        while let breakIndex = pendingSpeechText.firstIndex(where: { hardSpeechBreaks.contains($0) }) {
            let end = pendingSpeechText.index(after: breakIndex)
            appendSpeechSegment(String(pendingSpeechText[..<end]), to: &segments)
            pendingSpeechText = String(pendingSpeechText[end...])
        }

        if force {
            appendSpeechSegment(pendingSpeechText, to: &segments)
            pendingSpeechText = ""
        } else if pendingSpeechText.count >= 48 {
            if let breakIndex = pendingSpeechText.firstIndex(where: { softSpeechBreaks.contains($0) }) {
                let end = pendingSpeechText.index(after: breakIndex)
                appendSpeechSegment(String(pendingSpeechText[..<end]), to: &segments)
                pendingSpeechText = String(pendingSpeechText[end...])
            } else if pendingSpeechText.count >= 72 {
                let end = pendingSpeechText.index(pendingSpeechText.startIndex, offsetBy: 48)
                appendSpeechSegment(String(pendingSpeechText[..<end]), to: &segments)
                pendingSpeechText = String(pendingSpeechText[end...])
            }
        }
        return segments
    }

    private func appendSpeechSegment(_ rawText: String, to segments: inout [String]) {
        let cleaned = spokenText(rawText)
        if !cleaned.isEmpty {
            segments.append(cleaned)
        }
    }

    private func spokenText(_ text: String) -> String {
        text
            .replacingOccurrences(of: #"https?://\S+"#, with: "", options: .regularExpression)
            .replacingOccurrences(of: #"\[\d+\]"#, with: "", options: .regularExpression)
            .replacingOccurrences(of: #"[*_`#>-]"#, with: "", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func startTTSWorkerIfNeeded(settings: AppSettings) {
        guard ttsWorkerTask == nil, !queuedSpeechSegments.isEmpty else { return }
        ttsWorkerTask = Task { [weak self, settings] in
            await self?.runTTSQueue(settings: settings)
        }
    }

    private func runTTSQueue(settings: AppSettings) async {
        defer {
            ttsWorkerTask = nil
            isSynthesizing = false
            synthesisTicker?.cancel()
            synthesisTicker = nil
            if !Task.isCancelled,
               settings.handsFreeMode,
               !isSending,
               !isListening,
               queuedSpeechSegments.isEmpty {
                startListening(settings: settings)
            }
        }

        while !Task.isCancelled {
            guard !queuedSpeechSegments.isEmpty else { break }
            let segment = queuedSpeechSegments.removeFirst()
            let completed = await synthesizeAndPlaySegment(segment, settings: settings)
            if !completed {
                break
            }
        }
    }

    private func synthesizeAndPlaySegment(_ text: String, settings: AppSettings) async -> Bool {
        let cleanText = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanText.isEmpty else { return true }
        isSynthesizing = true
        synthesisElapsed = 0
        let started = Date()
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
            let result = try await api.synthesize(text: cleanText, settings: settings)
            synthesisTicker?.cancel()
            isSynthesizing = false
            var metrics = result.metrics
            metrics.elapsedSeconds = Date().timeIntervalSince(started)
            lastTTSMetrics = metrics
            try await player.playAndWait(fileURL: result.fileURL)
            return !Task.isCancelled
        } catch is CancellationError {
            return false
        } catch {
            synthesisTicker?.cancel()
            isSynthesizing = false
            errorMessage = "语音合成失败：\(error.localizedDescription)"
            return false
        }
    }
}
