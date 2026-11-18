import AppKit

@MainActor
final class SpriteWindow: NSWindow {
    var onPress: (() -> Void)?
    var onMove: ((NSPoint, NSPoint) -> Void)?
    var onMoveFinished: (() -> Void)?

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }

    override func sendEvent(_ event: NSEvent) {
        guard event.type == .leftMouseDown else {
            super.sendEvent(event)
            return
        }
        let start = convertPoint(toScreen: event.locationInWindow)
        let origin = frame.origin
        var dragged = false
        // Keep tracking at the window level so the SwiftUI artwork and button
        // do not have to receive a new event after the pointer leaves the sprite.
        let mask: NSEvent.EventTypeMask = [.leftMouseDragged, .leftMouseUp]
        while let next = nextEvent(matching: mask, until: Date(timeIntervalSinceNow: 30),
                                   inMode: .eventTracking, dequeue: true) {
            let cursor = next.window?.convertPoint(toScreen: next.locationInWindow) ?? next.locationInWindow
            let dx = cursor.x - start.x, dy = cursor.y - start.y
            if dragged || abs(dx) + abs(dy) > 3 {
                dragged = true
                onMove?(NSPoint(x: origin.x + dx, y: origin.y + dy), cursor)
            }
            if next.type == .leftMouseUp {
                if dragged { onMoveFinished?() } else { onPress?() }
                return
            }
        }
        if dragged { onMoveFinished?() }
    }
}
