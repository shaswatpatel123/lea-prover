import Mathlib

theorem sum_of_first_n_odd_numbers (n : ℕ) : (Finset.sum (Finset.range n) fun i => 2 * i + 1) = n^2 := by
  induction n with
  | zero =>
    -- Base case: prove the sum of first 0 odd numbers is 0^2
    simp
  | succ k ih =>
    -- Inductive step: assume true for k, prove for k + 1
    rw [Finset.sum_range_succ, ih, Nat.pow_succ]
    ring

