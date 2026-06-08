import Mathlib.Data.Nat.Basic

theorem sum_first_n_odd_numbers (n : ℕ) : (Finset.range n).sum (fun k => 2 * k + 1) = n * n := by sorry
