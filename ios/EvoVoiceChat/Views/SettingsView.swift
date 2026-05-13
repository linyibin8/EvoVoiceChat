import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var settings: AppSettings
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("后端") {
                    TextField("Backend URL", text: $settings.backendURL)
                        .textInputAutocapitalization(.never)
                        .keyboardType(.URL)
                }

                Section("搜索") {
                    Toggle("联网搜索", isOn: $settings.searchEnabled)
                    Stepper("结果数 \(settings.maxSearchResults)", value: $settings.maxSearchResults, in: 1...10)
                    TextField("指定网站来源", text: $settings.sourceDomains, axis: .vertical)
                        .textInputAutocapitalization(.never)
                        .lineLimit(2...5)
                }

                Section("语音") {
                    Toggle("连续语音对话", isOn: $settings.handsFreeMode)
                    Toggle("优先本机识别", isOn: $settings.preferOnDeviceSpeech)
                    TextField("TTS Voice", text: $settings.ttsVoice)
                        .textInputAutocapitalization(.never)
                }
            }
            .navigationTitle("设置")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") {
                        dismiss()
                    }
                }
            }
        }
    }
}
