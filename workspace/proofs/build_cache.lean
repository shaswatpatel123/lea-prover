import Init
#eval do
  let out ← IO.Process.output { cmd := "lake", args := #["exe", "cache", "get"] }
  IO.println out.stdout
  IO.println out.stderr
