// make-icon — renders the app icon with AppKit into an .iconset directory.
// Build step only (needs the Swift toolchain); the app itself never uses it.
import AppKit
import Foundation

let outDir = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "AppIcon.iconset"
let fm = FileManager.default
try? fm.createDirectory(atPath: outDir, withIntermediateDirectories: true)

func drawIcon(size: CGFloat) -> NSBitmapImageRep {
    let rep = NSBitmapImageRep(
        bitmapDataPlanes: nil,
        pixelsWide: Int(size), pixelsHigh: Int(size),
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0
    )!
    rep.size = NSSize(width: size, height: size)

    NSGraphicsContext.saveGraphicsState()
    let ctx = NSGraphicsContext(bitmapImageRep: rep)!
    NSGraphicsContext.current = ctx
    ctx.imageInterpolation = .high

    let s = size
    // macOS squircle-ish rounded rect with the standard 10% margin.
    let inset = s * 0.06
    let rect = NSRect(x: inset, y: inset, width: s - inset * 2, height: s - inset * 2)
    let path = NSBezierPath(roundedRect: rect, xRadius: s * 0.225, yRadius: s * 0.225)

    let top = NSColor(srgbRed: 0.18, green: 0.42, blue: 0.78, alpha: 1)
    let bottom = NSColor(srgbRed: 0.36, green: 0.33, blue: 0.82, alpha: 1)
    let gradient = NSGradient(starting: top, ending: bottom)!
    gradient.draw(in: path, angle: -90)

    // Inner glow.
    let glow = NSBezierPath(roundedRect: rect.insetBy(dx: s * 0.012, dy: s * 0.012),
                            xRadius: s * 0.215, yRadius: s * 0.215)
    NSColor(white: 1, alpha: 0.16).setStroke()
    glow.lineWidth = max(1, s * 0.012)
    glow.stroke()

    // Everything below stays inside the rounded shape.
    NSGraphicsContext.saveGraphicsState()
    path.addClip()

    // Sun.
    let sunSize = s * 0.15
    let sunRect = NSRect(x: rect.minX + s * 0.18, y: rect.maxY - s * 0.34, width: sunSize, height: sunSize)
    NSColor(srgbRed: 1, green: 0.93, blue: 0.66, alpha: 1).setFill()
    NSBezierPath(ovalIn: sunRect).fill()

    // Two mountain silhouettes.
    func mountain(_ points: [NSPoint], _ color: NSColor) {
        let p = NSBezierPath()
        p.move(to: points[0])
        for pt in points.dropFirst() { p.line(to: pt) }
        p.close()
        color.setFill()
        p.fill()
    }

    let base = rect.minY + s * 0.12
    mountain([
        NSPoint(x: rect.minX - s * 0.02, y: base),
        NSPoint(x: rect.minX + s * 0.30, y: base + s * 0.30),
        NSPoint(x: rect.minX + s * 0.52, y: base + s * 0.06),
        NSPoint(x: rect.minX + s * 0.68, y: base + s * 0.22),
        NSPoint(x: rect.maxX + s * 0.02, y: base + s * 0.02),
        NSPoint(x: rect.maxX + s * 0.02, y: base - s * 0.20),
        NSPoint(x: rect.minX - s * 0.02, y: base - s * 0.20),
    ], NSColor(srgbRed: 0.07, green: 0.16, blue: 0.32, alpha: 0.55))

    mountain([
        NSPoint(x: rect.minX - s * 0.02, y: base - s * 0.02),
        NSPoint(x: rect.minX + s * 0.22, y: base + s * 0.12),
        NSPoint(x: rect.minX + s * 0.44, y: base - s * 0.04),
        NSPoint(x: rect.minX + s * 0.62, y: base + s * 0.08),
        NSPoint(x: rect.maxX + s * 0.02, y: base - s * 0.06),
        NSPoint(x: rect.maxX + s * 0.02, y: rect.minY - s * 0.02),
        NSPoint(x: rect.minX - s * 0.02, y: rect.minY - s * 0.02),
    ], NSColor(srgbRed: 0.04, green: 0.09, blue: 0.20, alpha: 0.85))

    NSGraphicsContext.restoreGraphicsState()
    return rep
}

let variants: [(String, CGFloat)] = [
    ("icon_16x16", 16), ("icon_16x16@2x", 32),
    ("icon_32x32", 32), ("icon_32x32@2x", 64),
    ("icon_128x128", 128), ("icon_128x128@2x", 256),
    ("icon_256x256", 256), ("icon_256x256@2x", 512),
    ("icon_512x512", 512), ("icon_512x512@2x", 1024),
]

for (name, size) in variants {
    let rep = drawIcon(size: size)
    guard let data = rep.representation(using: .png, properties: [:]) else {
        fputs("failed to encode \(name)\n", stderr); exit(1)
    }
    try data.write(to: URL(fileURLWithPath: "\(outDir)/\(name).png"))
}
print("icons written to \(outDir)")
