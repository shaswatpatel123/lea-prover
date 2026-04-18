def runCache : IO Unit := do
  let out ← IO.Process.run { cmd := "lake", args := #["exe", "cache", "get"] }
  IO.println out

#eval runCache
