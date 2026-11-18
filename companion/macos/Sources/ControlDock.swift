import CoreGraphics
import Foundation

enum ControlDock {
    case above, below

    static func choose(sprite: CGRect, bounds: CGRect, height: CGFloat) -> ControlDock {
        let above = bounds.maxY - sprite.maxY - 18
        let below = sprite.minY - bounds.minY - 18
        if below >= height { return .below }
        if above >= height { return .above }
        return above >= below ? .above : .below
    }

    func clampHeight(_ desired: CGFloat, sprite: CGRect, bounds: CGRect) -> CGFloat {
        let room = self == .above ? bounds.maxY - sprite.maxY - 18 : sprite.minY - bounds.minY - 18
        return min(desired, max(190, room))
    }

    func frame(sprite: CGRect, bounds: CGRect, size: CGSize) -> CGRect {
        let width = min(size.width, bounds.width - 20)
        let x = min(max(sprite.midX - width / 2, bounds.minX + 10), bounds.maxX - width - 10)
        let y = self == .above ? sprite.maxY + 8 : sprite.minY - size.height - 8
        return CGRect(x: x, y: min(max(y, bounds.minY + 10), bounds.maxY - size.height - 10),
                      width: width, height: size.height)
    }
}
