// wallpaperctl — native macOS wallpaper helper for DS Bing Wallpaper.
//
// Uses NSWorkspace.setDesktopImageURL, which is the official API and needs no
// Automation (AppleEvents) permission, unlike `osascript ... System Events`.
//
// Usage:
//   wallpaperctl get            print the primary screen wallpaper path
//   wallpaperctl get --json     print JSON for every screen
//   wallpaperctl set <path>     set <path> as wallpaper on every screen
//
// Exit codes: 0 ok, 2 usage, 3 missing file, 4 set failed.

import AppKit
import Foundation

func screenInfo() -> [[String: Any]] {
    let ws = NSWorkspace.shared
    return NSScreen.screens.enumerated().map { index, screen in
        [
            "index": index,
            "screen": "\(Int(screen.frame.width))x\(Int(screen.frame.height))",
            "path": ws.desktopImageURL(for: screen)?.path ?? "",
        ]
    }
}

func emitJSON(_ object: Any) {
    guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys]),
          let text = String(data: data, encoding: .utf8) else {
        fputs("error: cannot encode json\n", stderr)
        exit(5)
    }
    print(text)
}

let args = CommandLine.arguments
guard args.count >= 2 else {
    fputs("usage: wallpaperctl get|set <path>\n", stderr)
    exit(2)
}

switch args[1] {
case "get":
    let screens = screenInfo()
    if args.contains("--json") {
        emitJSON(["screens": screens])
    } else {
        // Primary screen path first so shell callers can read one line.
        print(screens.first?["path"] as? String ?? "")
    }
    exit(0)

case "set":
    guard args.count >= 3 else {
        fputs("usage: wallpaperctl set <path>\n", stderr)
        exit(2)
    }
    let path = args[2]
    let url = URL(fileURLWithPath: path)
    guard FileManager.default.fileExists(atPath: url.path) else {
        fputs("error: no such file: \(path)\n", stderr)
        exit(3)
    }
    let ws = NSWorkspace.shared
    var failed = false
    var lastError = ""
    for screen in NSScreen.screens {
        do {
            try ws.setDesktopImageURL(url, for: screen, options: [:])
        } catch {
            failed = true
            lastError = "\(error)"
            fputs("error: screen \(Int(screen.frame.width))x\(Int(screen.frame.height)): \(error)\n", stderr)
        }
    }
    if failed {
        // Some transient WindowServer states fail once; a single retry helps.
        usleep(200_000)
        if let main = NSScreen.main, (try? ws.setDesktopImageURL(url, for: main, options: [:])) != nil {
            failed = false
        } else {
            fputs("error: failed to set wallpaper: \(lastError)\n", stderr)
            exit(4)
        }
    }
    print("ok \(path)")
    exit(0)

default:
    fputs("unknown command: \(args[1])\n", stderr)
    exit(2)
}
