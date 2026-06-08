import Mathlib

theorem sum_first_n_odds (n : ℕ) : (Finset.range n).sum (fun i => 2 * i + 1) = n ^ 2 := by sorry
