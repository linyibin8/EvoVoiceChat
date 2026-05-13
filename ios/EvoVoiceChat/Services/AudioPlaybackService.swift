import AVFoundation
import Foundation

final class AudioPlaybackService: NSObject, AVAudioPlayerDelegate {
    private var player: AVAudioPlayer?
    private var completion: (() -> Void)?
    var onStateChange: ((Bool) -> Void)?

    func play(fileURL: URL, completion: (() -> Void)? = nil) throws {
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
                    try play(fileURL: fileURL) {
                        continuation.resume()
                    }
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        } onCancel: {
            self.stop()
        }
    }

    func stop() {
        let completion = completion
        self.completion = nil
        player?.stop()
        player = nil
        onStateChange?(false)
        completion?()
    }

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        self.player = nil
        onStateChange?(false)
        let completion = self.completion
        self.completion = nil
        completion?()
    }
}
