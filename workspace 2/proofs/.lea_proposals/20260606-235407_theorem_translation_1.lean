import Mathlib

theorem sum_of_first_n_odd_numbers_eq_n_squared (n : ℕ) :
  (Finset.range n).sum (fun k => 2 * k + 1) = n * n := by sorry
