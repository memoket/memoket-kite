import CoreGraphics
import Foundation

@main
struct DockRegression {
    static func main() {
        let screen = CGRect(x: 0, y: 0, width: 1400, height: 800)
        let sprite = CGRect(x: 1190, y: 390, width: 78, height: 78)
        let dock = ControlDock.choose(sprite: sprite, bounds: screen, height: 200)
        precondition(dock == .below)
        let before = dock.frame(sprite: sprite, bounds: screen, size: CGSize(width: 400, height: 200))
        let after = dock.frame(sprite: sprite, bounds: screen, size: CGSize(
            width: 400, height: dock.clampHeight(620, sprite: sprite, bounds: screen)
        ))
        precondition(before.maxY == after.maxY && after.maxY < sprite.minY)
        precondition(screen.contains(after))

        let low = CGRect(x: 50, y: 42, width: 78, height: 78)
        let highDock = ControlDock.choose(sprite: low, bounds: screen, height: 200)
        precondition(highDock == .above)
        let highA = highDock.frame(sprite: low, bounds: screen, size: CGSize(width: 400, height: 200))
        let highB = highDock.frame(sprite: low, bounds: screen, size: CGSize(width: 400, height: 600))
        precondition(highA.minY == highB.minY && highB.minY > low.maxY)
        precondition(screen.contains(highB))
        print("Panel stays anchored on both sides")
    }
}
