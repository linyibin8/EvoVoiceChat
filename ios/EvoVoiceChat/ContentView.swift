import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var settings: AppSettings
    @StateObject private var viewModel = ChatViewModel()
    @State private var showingSettings = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                HeaderView(healthText: viewModel.healthText, showingSettings: $showingSettings)
                ModeBarView()
                MessageListView(messages: viewModel.messages)
                if !viewModel.liveTranscript.isEmpty || viewModel.isListening {
                    TranscriptView(text: viewModel.liveTranscript, isListening: viewModel.isListening)
                }
                MetricsBarView(
                    isSending: viewModel.isSending,
                    isSynthesizing: viewModel.isSynthesizing,
                    isPlaying: viewModel.isPlaying,
                    synthesisElapsed: viewModel.synthesisElapsed,
                    ttsMetrics: viewModel.lastTTSMetrics,
                    timings: viewModel.lastTimings
                )
                if let error = viewModel.errorMessage {
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .lineLimit(2)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 6)
                }
                InputBarView(
                    text: $viewModel.inputText,
                    isListening: viewModel.isListening,
                    isBusy: viewModel.isSending || viewModel.isSynthesizing || viewModel.isPlaying,
                    onSend: { viewModel.sendTapped(settings: settings) },
                    onMicDown: { viewModel.startListening(settings: settings) },
                    onMicUp: { viewModel.stopListeningAndSend(settings: settings) },
                    onInterrupt: { viewModel.interrupt() }
                )
            }
            .background(Color(.systemGroupedBackground))
            .navigationBarHidden(true)
            .sheet(isPresented: $showingSettings) {
                SettingsView()
                    .environmentObject(settings)
            }
            .task {
                viewModel.checkHealth(settings: settings)
            }
            .onChange(of: settings.backendURL) { _, _ in
                viewModel.checkHealth(settings: settings)
            }
        }
    }
}

private struct HeaderView: View {
    let healthText: String
    @Binding var showingSettings: Bool

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Evo Voice")
                    .font(.system(size: 24, weight: .semibold))
                Text(healthText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer()
            Button {
                showingSettings = true
            } label: {
                Image(systemName: "slider.horizontal.3")
                    .font(.system(size: 18, weight: .semibold))
                    .frame(width: 42, height: 42)
            }
            .buttonStyle(.bordered)
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .padding(.horizontal, 16)
        .padding(.top, 14)
        .padding(.bottom, 10)
        .background(Color(.systemBackground))
    }
}

private struct ModeBarView: View {
    @EnvironmentObject private var settings: AppSettings

    var body: some View {
        HStack(spacing: 10) {
            Toggle(isOn: $settings.searchEnabled) {
                Label("联网", systemImage: "magnifyingglass")
            }
            .toggleStyle(.button)

            Toggle(isOn: $settings.handsFreeMode) {
                Label("连聊", systemImage: "waveform.and.mic")
            }
            .toggleStyle(.button)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(settings.parsedSourceDomains.prefix(5), id: \.self) { domain in
                        Text(domain)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 5)
                            .background(Color(.secondarySystemGroupedBackground))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(Color(.systemBackground))
    }
}

private struct MessageListView: View {
    let messages: [ChatMessage]

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 12) {
                    ForEach(messages) { message in
                        ChatBubbleView(message: message)
                            .id(message.id)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 14)
            }
            .onChange(of: messages.count) { _, _ in
                scrollToLast(proxy)
            }
            .onChange(of: messages.last?.content) { _, _ in
                scrollToLast(proxy)
            }
        }
    }

    private func scrollToLast(_ proxy: ScrollViewProxy) {
        if let last = messages.last {
            withAnimation(.easeOut(duration: 0.2)) {
                proxy.scrollTo(last.id, anchor: .bottom)
            }
        }
    }
}

private struct ChatBubbleView: View {
    let message: ChatMessage

    var isUser: Bool { message.role == .user }

    var body: some View {
        HStack(alignment: .bottom) {
            if isUser { Spacer(minLength: 36) }
            VStack(alignment: .leading, spacing: 8) {
                Text(displayText)
                    .font(.body)
                    .foregroundStyle(isUser ? Color.white : Color.primary)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                if !message.sources.isEmpty {
                    SourcesView(sources: message.sources)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .background(isUser ? Color.accentColor : Color(.systemBackground))
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .shadow(color: Color.black.opacity(0.04), radius: 5, y: 2)
            if !isUser { Spacer(minLength: 36) }
        }
    }

    private var displayText: String {
        if message.content.isEmpty, !isUser {
            return "..."
        }
        return message.content
    }
}

private struct SourcesView: View {
    let sources: [SearchResult]

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(Array(sources.prefix(4).enumerated()), id: \.element.id) { index, source in
                Link(destination: URL(string: source.link) ?? URL(string: "https://news.google.com")!) {
                    HStack(alignment: .top, spacing: 6) {
                        Text("[\(index + 1)]")
                            .font(.caption.weight(.semibold))
                        VStack(alignment: .leading, spacing: 2) {
                            Text(source.title)
                                .font(.caption)
                                .lineLimit(2)
                            if let name = source.source {
                                Text(name)
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                .tint(.primary)
            }
        }
        .padding(8)
        .background(Color(.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

private struct TranscriptView: View {
    let text: String
    let isListening: Bool

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: isListening ? "mic.fill" : "mic")
                .foregroundStyle(isListening ? .red : .secondary)
            Text(text.isEmpty ? "正在听..." : text)
                .font(.callout)
                .lineLimit(3)
            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(Color(.systemBackground))
    }
}

private struct MetricsBarView: View {
    let isSending: Bool
    let isSynthesizing: Bool
    let isPlaying: Bool
    let synthesisElapsed: Double
    let ttsMetrics: TTSMetrics
    let timings: [String: Double]

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                if isSending {
                    MetricPill(icon: "brain.head.profile", text: "思考中")
                }
                if let search = timings["search"] {
                    MetricPill(icon: "magnifyingglass", text: "搜索 \(formatMs(search))")
                }
                if let llm = timings["llm"] {
                    MetricPill(icon: "cpu", text: "LLM \(formatMs(llm))")
                }
                if isSynthesizing {
                    MetricPill(icon: "speaker.wave.2", text: "TTS \(String(format: "%.1fs", synthesisElapsed))")
                } else if ttsMetrics.hasFinalMetrics {
                    MetricPill(icon: "speaker.wave.2", text: "TTS \(String(format: "%.1fs", ttsMetrics.latencyMs / 1000))")
                    MetricPill(icon: "waveform", text: "音频 \(String(format: "%.1fs", ttsMetrics.audioDurationSeconds))")
                    MetricPill(icon: "speedometer", text: "RTF \(String(format: "%.2f", ttsMetrics.rtf))")
                    MetricPill(icon: "textformat", text: "\(String(format: "%.1f", ttsMetrics.charsPerSecond))字/s")
                }
                if isPlaying {
                    MetricPill(icon: "play.circle.fill", text: "播放中")
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
        }
        .background(Color(.systemBackground))
    }

    private func formatMs(_ value: Double) -> String {
        if value >= 1000 {
            return String(format: "%.1fs", value / 1000)
        }
        return String(format: "%.0fms", value)
    }
}

private struct MetricPill: View {
    let icon: String
    let text: String

    var body: some View {
        Label(text, systemImage: icon)
            .font(.caption)
            .foregroundStyle(.primary)
            .padding(.horizontal, 9)
            .padding(.vertical, 6)
            .background(Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

private struct InputBarView: View {
    @Binding var text: String
    let isListening: Bool
    let isBusy: Bool
    let onSend: () -> Void
    let onMicDown: () -> Void
    let onMicUp: () -> Void
    let onInterrupt: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            TextField("问点什么，或按住说话", text: $text, axis: .vertical)
                .lineLimit(1...4)
                .textFieldStyle(.plain)
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(Color(.secondarySystemGroupedBackground))
                .clipShape(RoundedRectangle(cornerRadius: 8))

            Button {
                isBusy ? onInterrupt() : onSend()
            } label: {
                Image(systemName: isBusy ? "stop.fill" : "paperplane.fill")
                    .font(.system(size: 17, weight: .semibold))
                    .frame(width: 42, height: 42)
            }
            .buttonStyle(.borderedProminent)
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !isBusy)

            PressToTalkButton(isListening: isListening, onStart: onMicDown, onStop: onMicUp)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(Color(.systemBackground))
    }
}

private struct PressToTalkButton: View {
    let isListening: Bool
    let onStart: () -> Void
    let onStop: () -> Void
    @State private var isPressed = false

    var body: some View {
        Image(systemName: isListening ? "mic.fill" : "mic")
            .font(.system(size: 18, weight: .semibold))
            .foregroundStyle(isListening ? Color.red : Color.accentColor)
            .frame(width: 46, height: 46)
            .background(Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(isListening ? Color.red.opacity(0.55) : Color.accentColor.opacity(0.25), lineWidth: 1)
            )
            .contentShape(RoundedRectangle(cornerRadius: 8))
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { _ in
                        guard !isPressed else { return }
                        isPressed = true
                        onStart()
                    }
                    .onEnded { _ in
                        isPressed = false
                        onStop()
                    }
            )
    }
}
