import AVFoundation
import Foundation

@MainActor
final class AudioPlaybackService: NSObject, @preconcurrency AVAudioPlayerDelegate {
    private var player: AVAudioPlayer?
    private var completion: ((Bool) -> Void)?
    var onStateChange: ((Bool) -> Void)?

    func play(fileURL: URL, completion: ((Bool) -> Void)? = nil) throws {
        stop()
        let audioSession = AVAudioSession.sharedInstance()
        try audioSession.setCategory(.playback, mode: .spokenAudio, options: [.duckOthers])
        try audioSession.setActive(true)
        self.completion = completion
        let player = try AVAudioPlayer(contentsOf: fileURL)
        player.delegate = self
        player.prepareToPlay()
        self.player = player
        onStateChange?(true)
        player.play()
    }

    func playAndWait(fileURL: URL) async throws {
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                do {
                    try play(fileURL: fileURL) { _ in
                        continuation.resume()
                    }
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        } onCancel: {
            Task { @MainActor in
                self.stop()
            }
        }
    }

    func stop() {
        player?.stop()
        player = nil
        let completion = completion
        self.completion = nil
        onStateChange?(false)
        completion?(false)
    }

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        self.player = nil
        let completion = completion
        self.completion = nil
        onStateChange?(false)
        completion?(flag)
    }
}
