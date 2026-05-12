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
                let assistant = ChatMessage(role: .assistant, content: response.assistant_text, sources: response.search_results)
                messages.append(assistant)
                isSending = false
                if speakAnswer {
                    await synthesizeAndPlay(response.assistant_text, settings: settings)
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
        let cleanText = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanText.isEmpty else { return }
        isSynthesizing = true
        synthesisElapsed = 0
        lastTTSMetrics = TTSMetrics()
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
            try player.play(fileURL: result.fileURL) { [weak self, weak settings] in
                guard let self, let settings else { return }
                Task { @MainActor in
                    if settings.handsFreeMode {
                        self.startListening(settings: settings)
                    }
                }
            }
        } catch {
            synthesisTicker?.cancel()
            isSynthesizing = false
            errorMessage = "语音合成失败：\(error.localizedDescription)"
        }
    }
}
