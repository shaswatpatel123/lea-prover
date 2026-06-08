import Mathlib

theorem sum_of_first_n_odd_numbers (n : ℕ) : 
  (Finset.range n).sum (fun k => 2 * k + 1) = n * n := by
  induction n with
  | zero =>
    -- Base case: n = 0
    rw [Finset.range_zero, Finset.sum_empty]
    norm_num
  | succ n ih =>
    -- Inductive step: assuming it holds for n, prove for n + 1
    rw [Finset.sum_range_succ, ih]
    -- goal: (n + 1)^2 = n^2 + (2n + 1)
    ring
