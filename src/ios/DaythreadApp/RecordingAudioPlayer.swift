import SwiftUI
import AVFoundation
import Observation
import OmiCore

/// Owned by the recording screen so scrolling the transcript keeps audio playing.
@MainActor @Observable
final class RecordingPlayback {
    private(set) var duration: TimeInterval = 0
    private(set) var position: TimeInterval = 0
    private(set) var peaks: [Float] = []
    private(set) var playing = false
    private(set) var loading = false
    private(set) var ready = false
    private(set) var speed: Double = 1
    private(set) var error: String?
    private(set) var waveformUnavailable = false
    @ObservationIgnored private weak var model: AppModel?
    @ObservationIgnored private var fileURL: URL?
    @ObservationIgnored private var requestID = UUID()
    @ObservationIgnored private var progressTask: Task<Void, Never>?
    @ObservationIgnored private var scrubbing = false
    @ObservationIgnored private var resumeAfterScrub = false

    private var player: AVAudioPlayer? {
        guard let fileURL, model?.player?.url == fileURL else { return nil }
        return model?.player
    }

    func load(sourceID: String, durationHint: TimeInterval, model: AppModel, audioPath: String? = nil) async {
        close()
        self.model = model
        duration = max(0, durationHint.isFinite ? durationHint : 0)
        position = 0; peaks = []; error = nil; waveformUnavailable = false; loading = true
        let request = requestID, identity = model.archiveIdentity
        var downloaded: URL?
        defer {
            if let downloaded { try? FileManager.default.removeItem(at: downloaded) }
            if requestID == request { loading = false }
        }
        do {
            let audio = try await model.client().download(audioPath ?? "v1/audio/\(sourceID)", query: [:])
            downloaded = audio
            try Task.checkCancellation()
            let waveformTask = Task.detached(priority: .utility) { try RecordingWaveform.read(url: audio) }
            do {
                let waveform = try await withTaskCancellationHandler {
                    try await waveformTask.value
                } onCancel: { waveformTask.cancel() }
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
                peaks = waveform.peaks; duration = waveform.duration
            } catch {
                try Task.checkCancellation()
                guard requestID == request, model.archiveIdentity == identity else { return }
                waveformUnavailable = true
                if let audioDuration = try? AVAudioPlayer(contentsOf: audio).duration, audioDuration.isFinite {
                    duration = max(0, audioDuration)
                }
            }
            guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
            fileURL = audio; downloaded = nil; ready = true
            progressTask = Task { [weak self] in
                while !Task.isCancelled {
                    self?.updatePosition()
                    do { try await Task.sleep(for: .milliseconds(200)) } catch { return }
                }
            }
        } catch {
            guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
            self.error = L10n.error(error)
        }
    }

    func togglePlayback() {
        guard ready, let fileURL, let model else { return }
        guard model.microphone.active == nil else {
            error = L10n.text("Stop recording before playing audio."); return
        }
        error = nil
        if let player {
            if player.isPlaying { player.pause(); playing = false }
            else { playing = player.play() }
        } else {
            if position >= duration { position = 0 }
            model.play(fileURL, startAt: position, rate: Float(speed))
            playing = player?.isPlaying == true
            if !playing { error = model.error ?? L10n.text("Unable to play recording.") }
        }
    }

    func seek(to seconds: TimeInterval) {
        guard ready, seconds.isFinite else { return }
        position = min(duration, max(0, seconds))
        if position >= duration {
            if player != nil { model?.stopPlayback() }
            playing = false
        } else { player?.currentTime = position }
    }

    func skip(_ seconds: TimeInterval) { seek(to: position + seconds) }

    func changeSpeed(_ delta: Double) {
        speed = min(2, max(0.5, speed + delta))
        player?.rate = Float(speed)
    }

    func setScrubbing(_ active: Bool) {
        if active && !scrubbing {
            resumeAfterScrub = playing
            player?.pause(); playing = false
        }
        scrubbing = active
        if !active {
            if resumeAfterScrub && position < duration { togglePlayback() }
            resumeAfterScrub = false
        }
    }

    func close() {
        requestID = UUID(); progressTask?.cancel(); progressTask = nil
        if player != nil { model?.stopPlayback() }
        if let fileURL { try? FileManager.default.removeItem(at: fileURL) }
        fileURL = nil; ready = false; playing = false; loading = false
        scrubbing = false; resumeAfterScrub = false
    }

    private func updatePosition() {
        guard !scrubbing else { return }
        if let player {
            position = min(duration, max(0, player.currentTime)); playing = player.isPlaying
        } else if playing {
            playing = false
            if duration - position < 1 { position = duration }
        }
    }
}

struct RecordingAudioPlayer: View {
    let playback: RecordingPlayback
    let retry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 4) {
                Text(time(playback.position)).foregroundStyle(.primary)
                Text("/ " + time(playback.duration)).foregroundStyle(.secondary)
                Spacer()
            }.font(.subheadline.monospacedDigit()).accessibilityElement(children: .combine)
                .accessibilityIdentifier("source.playbackTime")
            if playback.loading {
                ProgressView(L10n.text("Loading audio…")).frame(maxWidth: .infinity, minHeight: 64)
            } else if playback.waveformUnavailable {
                Text(L10n.text("Waveform unavailable")).font(.caption).foregroundStyle(.secondary)
            } else {
                waveform.frame(height: 64)
                    .accessibilityElement()
                    .accessibilityLabel(L10n.text("Playback position"))
                    .accessibilityValue(time(playback.position) + " / " + time(playback.duration))
                    .accessibilityAdjustableAction { direction in
                        switch direction {
                        case .increment: playback.skip(15)
                        case .decrement: playback.skip(-15)
                        @unknown default: break
                        }
                    }
                    .accessibilityIdentifier("source.playbackPosition")
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) { transport; Spacer(minLength: 8); speedControls }
                VStack(spacing: 8) { HStack { transport }; HStack { speedControls } }
            }
            ErrorNote(message: playback.error)
            if !playback.loading && !playback.ready {
                Button(L10n.text("Try again"), action: retry).accessibilityIdentifier("source.retryAudio")
            }
        }.padding(.vertical, 8)
    }

    private var transport: some View {
        HStack(spacing: 6) {
            Button(action: playback.togglePlayback) {
                Image(systemName: playback.playing ? "pause.fill" : "play.fill")
                    .font(.title2).frame(width: 48, height: 48)
                    .foregroundStyle(.white).background(Theme.accent, in: Circle())
            }.accessibilityLabel(playback.playing ? L10n.text("Pause playback") : L10n.text("Play original audio"))
                .accessibilityIdentifier("source.playAudio")
            Button { playback.skip(-15) } label: { Image(systemName: "gobackward.15").font(.title2).frame(width: 44, height: 44) }
                .accessibilityLabel(L10n.text("Skip backward 15 seconds")).accessibilityIdentifier("source.skipBackward")
            Button { playback.skip(15) } label: { Image(systemName: "goforward.15").font(.title2).frame(width: 44, height: 44) }
                .accessibilityLabel(L10n.text("Skip forward 15 seconds")).accessibilityIdentifier("source.skipForward")
        }.buttonStyle(.plain).disabled(!playback.ready || playback.duration == 0)
    }

    private var speedControls: some View {
        HStack(spacing: 0) {
            Button { playback.changeSpeed(-0.25) } label: { Image(systemName: "minus.circle").frame(width: 44, height: 44) }
                .disabled(playback.speed <= 0.5).accessibilityLabel(L10n.text("Slower playback"))
                .accessibilityIdentifier("source.slowerAudio")
            Text(playback.speed.formatted(.number.locale(L10n.locale).precision(.fractionLength(0...2))) + "×")
                .font(.subheadline.monospacedDigit()).frame(minWidth: 32)
                .accessibilityLabel(L10n.text("Playback speed")).accessibilityValue(String(playback.speed))
                .accessibilityIdentifier("source.playbackSpeed")
            Button { playback.changeSpeed(0.25) } label: { Image(systemName: "plus.circle").frame(width: 44, height: 44) }
                .disabled(playback.speed >= 2).accessibilityLabel(L10n.text("Faster playback"))
                .accessibilityIdentifier("source.fasterAudio")
        }.buttonStyle(.plain)
    }

    private var waveform: some View {
        GeometryReader { geometry in
            Canvas { context, size in
                guard !playback.peaks.isEmpty else { return }
                let step = size.width / CGFloat(playback.peaks.count)
                let fraction = playback.duration > 0 ? playback.position / playback.duration : 0
                for (index, peak) in playback.peaks.enumerated() {
                    let height = max(1, CGFloat(peak) * size.height)
                    let rect = CGRect(x: CGFloat(index) * step, y: (size.height - height) / 2,
                                      width: max(1, step * 0.55), height: height)
                    context.fill(Path(roundedRect: rect, cornerRadius: 1),
                                 with: .color(Double(index) / Double(playback.peaks.count) <= fraction ? Theme.accent : Color.secondary.opacity(0.28)))
                }
            }.contentShape(Rectangle())
                .gesture(DragGesture(minimumDistance: 0).onChanged { gesture in
                    playback.setScrubbing(true)
                    let fraction = Double(gesture.location.x / max(1, geometry.size.width))
                    playback.seek(to: playback.duration * min(1, max(0, fraction)))
                }.onEnded { _ in playback.setScrubbing(false) })
        }
    }

    private func time(_ interval: TimeInterval) -> String {
        let value = max(0, Int(interval.isFinite ? interval : 0))
        return value >= 3600 ? String(format: "%02d:%02d:%02d", value / 3600, value / 60 % 60, value % 60)
            : String(format: "%02d:%02d", value / 60, value % 60)
    }
}
