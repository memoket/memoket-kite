import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

final class AudioTracks: NSObject, SCStreamOutput {
    private struct Track {
        let url: URL
        let file: AVAudioFile
        let start: Double
    }

    private let folder: URL
    let queue = DispatchQueue(label: "org.memoket.kite.capture")
    private var tracks: [SCStreamOutputType: Track] = [:]
    private var failure: Error?

    init(folder: URL) { self.folder = folder }

    func stream(_ stream: SCStream, didOutputSampleBuffer sample: CMSampleBuffer, of type: SCStreamOutputType) {
        guard sample.isValid, sample.numSamples > 0, let description = sample.formatDescription else { return }
        do {
            let format = AVAudioFormat(cmAudioFormatDescription: description)
            let count = AVAudioFrameCount(sample.numSamples)
            guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: count) else { return }
            buffer.frameLength = count
            let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(sample, at: 0,
                            frameCount: Int32(count), into: buffer.mutableAudioBufferList)
            guard status == noErr else { throw TwinError("无法写入音频样本。") }
            if tracks[type] == nil {
                let url = folder.appendingPathComponent(type == .microphone ? "mic.caf" : "system.caf")
                let file = try AVAudioFile(forWriting: url, settings: format.settings,
                                           commonFormat: format.commonFormat, interleaved: format.isInterleaved)
                tracks[type] = Track(url: url, file: file, start: sample.presentationTimeStamp.seconds)
            }
            try tracks[type]?.file.write(from: buffer)
        } catch { failure = error }
    }

    func finish() throws -> [(url: URL, start: Double)] {
        try queue.sync {
            let result = tracks.values.map { ($0.url, $0.start) }.sorted { $0.0.path < $1.0.path }
            tracks.removeAll()
            if let failure { throw failure }
            if result.isEmpty { throw TwinError("没有采集到声音。请检查录音权限和输入设备。") }
            return result
        }
    }
}

@MainActor
final class Recorder {
    private var stream: SCStream?
    private var tracks: AudioTracks?
    private var folder: URL?
    private var started = Date()

    func start() async throws {
        guard await AVCaptureDevice.requestAccess(for: .audio) else { throw TwinError("请允许麦克风访问。") }
        let sources = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = sources.displays.first else { throw TwinError("没有可用于采集系统音频的屏幕。") }
        let output = try LocalProcess.scratch()
        let sink = AudioTracks(folder: output)
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let settings = SCStreamConfiguration()
        settings.capturesAudio = true
        settings.captureMicrophone = true
        settings.excludesCurrentProcessAudio = true
        settings.sampleRate = 48000
        let stream = SCStream(filter: filter, configuration: settings, delegate: nil)
        do {
            try stream.addStreamOutput(sink, type: .audio, sampleHandlerQueue: sink.queue)
            try stream.addStreamOutput(sink, type: .microphone, sampleHandlerQueue: sink.queue)
            try await stream.startCapture()
            self.stream = stream
            tracks = sink
            folder = output
            started = Date()
        } catch {
            try? FileManager.default.removeItem(at: output)
            throw error
        }
    }

    func stop() async throws -> Capture {
        guard let stream, let tracks, let folder else { throw TwinError("没有进行中的录音。") }
        defer { self.stream = nil; self.tracks = nil; self.folder = nil }
        do {
            try await stream.stopCapture()
            let files = try tracks.finish()
            let output = folder.appendingPathComponent("Meeting.m4a")
            try await Task.detached {
                let start = files.map(\.start).filter(\.isFinite).min() ?? 0
                var args = ["ffmpeg", "-nostdin", "-v", "error"]
                for file in files { args += ["-i", file.url.path] }
                if files.count > 1 {
                    let filters = files.enumerated().map { index, file in
                        let delay = file.start.isFinite ? Int(max(0, (file.start - start) * 1000).rounded()) : 0
                        return "[\(index):a]adelay=\(delay):all=1[a\(index)]"
                    }
                    args += ["-filter_complex", filters.joined(separator: ";") + ";[a0][a1]amix=inputs=2:duration=longest"]
                }
                args += ["-c:a", "aac", output.path]
                let result = try LocalProcess.run("/usr/bin/env", args)
                guard result.status == 0 else { throw TwinError(result.stderr) }
            }.value
            return Capture(kind: "recording", url: output, title: "会议录音", date: started, scratch: folder)
        } catch {
            throw TwinError("\(error.localizedDescription) 录音文件保存在 \(folder.path)")
        }
    }
}
