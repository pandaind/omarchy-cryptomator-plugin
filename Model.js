.pragma library

function parseStatus(raw) {
  if (!raw || typeof raw !== "string") {
    return { ok: false, lastError: "Empty status output" }
  }
  try {
    var data = JSON.parse(raw.trim())
    if (!data || typeof data !== "object") {
      return { ok: false, lastError: "Invalid JSON object" }
    }
    return {
      ok: data.ok === true,
      installed: data.installed === true,
      cliInstalled: data.cliInstalled === true,
      guiInstalled: data.guiInstalled === true,
      isBundled: data.isBundled === true,
      cliPath: String(data.cliPath || ""),
      running: data.running === true,
      totalVaults: Number(data.totalVaults || 0),
      unlockedCount: Number(data.unlockedCount || 0),
      vaults: Array.isArray(data.vaults) ? data.vaults : [],
      lastError: ""
    }
  } catch (e) {
    return { ok: false, lastError: "JSON parse error: " + e.message }
  }
}

function shortPath(path) {
  var p = String(path || "")
  var home = "/home/"
  var slash = p.indexOf("/", home.length)
  if (p.indexOf("/home/") === 0 && slash !== -1) {
    return "~" + p.substring(slash)
  }
  return p
}

function summaryText(unlockedCount, totalVaults, isInstalled, isRunning) {
  if (!isInstalled) return "Cryptomator CLI not installed"
  if (totalVaults === 0) return "No vaults configured"
  if (unlockedCount === 0) return "All " + totalVaults + " vaults locked"
  if (unlockedCount === totalVaults) return "All " + totalVaults + " vaults unlocked"
  return unlockedCount + " of " + totalVaults + " vaults unlocked"
}

var HERO_PHRASES = [
  "Safeguarding secrets",
  "Armoring vaults",
  "Shielding bytes",
  "Guarding keys",
  "Fortifying folders",
  "Cloaking files",
  "Locking down data"
]

function heroPhrase(index) {
  return HERO_PHRASES[Math.abs(index) % HERO_PHRASES.length]
}
