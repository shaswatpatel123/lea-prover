import Mathlib

theorem sum_of_first_n_odd_numbers (n : ℕ) : (Finset.range n).sum (fun k => 2 * k + 1) = n^2 := by sorry
