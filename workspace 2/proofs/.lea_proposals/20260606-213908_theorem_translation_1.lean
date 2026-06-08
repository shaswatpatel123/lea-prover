import Mathlib

theorem sum_of_first_n_odd_numbers (n : ℕ) : (Finset.sum (Finset.range n) fun i => 2 * i + 1) = n^2 := by sorry
