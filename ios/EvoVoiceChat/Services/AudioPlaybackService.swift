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

    func stop() {
        player?.stop()
        player = nil
        onStateChange?(false)
    }

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        self.player = nil
        onStateChange?(false)
        completion?()
        completion = nil
    }
}
