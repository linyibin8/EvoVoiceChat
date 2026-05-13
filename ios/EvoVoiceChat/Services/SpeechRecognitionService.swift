import AVFoundation
import Foundation
import Speech

@MainActor
final class SpeechRecognitionService: NSObject {
    private let audioEngine = AVAudioEngine()
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "zh-CN"))

    var onTranscript: ((String, Bool) -> Void)?
    var onStateChange: ((Bool) -> Void)?

    var isRecording: Bool {
        audioEngine.isRunning
    }

    func requestAuthorization() async -> Bool {
        await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status == .authorized)
            }
        }
    }

    func start(preferOnDevice: Bool = true) async throws {
        guard await requestAuthorization() else {
            throw SpeechError.notAuthorized
        }
        guard let recognizer else {
            throw SpeechError.recognizerUnavailable
        }
        stop()

        let audioSession = AVAudioSession.sharedInstance()
        try audioSession.setCategory(.playAndRecord, mode: .voiceChat, options: [.defaultToSpeaker, .allowBluetoothHFP])
        try audioSession.setActive(true, options: .notifyOthersOnDeactivation)

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        if preferOnDevice, recognizer.supportsOnDeviceRecognition {
            request.requiresOnDeviceRecognition = true
        }
        if #available(iOS 17.0, *) {
            request.addsPunctuation = true
        }
        recognitionRequest = request

        let inputNode = audioEngine.inputNode
        let format = inputNode.outputFormat(forBus: 0)
        inputNode.removeTap(onBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak request] buffer, _ in
            request?.append(buffer)
        }

        recognitionTask = recognizer.recognitionTask(with: request) { [weak self] result, error in
            if let result {
                let transcript = result.bestTranscription.formattedString
                Task { @MainActor in
                    self?.onTranscript?(transcript, result.isFinal)
                }
            }
            if error != nil || result?.isFinal == true {
                Task { @MainActor in
                    self?.onStateChange?(false)
                }
            }
        }

        audioEngine.prepare()
        try audioEngine.start()
        onStateChange?(true)
    }

    func stop() {
        if audioEngine.isRunning {
            audioEngine.stop()
            audioEngine.inputNode.removeTap(onBus: 0)
        }
        recognitionRequest?.endAudio()
        recognitionTask?.cancel()
        recognitionTask = nil
        recognitionRequest = nil
        onStateChange?(false)
    }
}

enum SpeechError: LocalizedError {
    case notAuthorized
    case recognizerUnavailable

    var errorDescription: String? {
        switch self {
        case .notAuthorized:
            return "没有语音识别权限"
        case .recognizerUnavailable:
            return "当前设备不可用语音识别"
        }
    }
}
