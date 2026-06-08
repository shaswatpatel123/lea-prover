import Mathlib

-- Proving that the sum of the first n odd numbers is n squared.
theorem sum_of_first_n_odd_numbers_eq_n_squared (n : ℕ) :
  (Finset.range n).sum (fun k => 2 * k + 1) = n * n := by
  induction n with
  | zero =>
    -- Base case: n = 0
    simp
  | succ n ih =>
    -- Induction step: Assume the result holds for n, prove for n+1
    calc
      (Finset.range (n + 1)).sum (fun k => 2 * k + 1)
          = (Finset.range n).sum (fun k => 2 * k + 1) + (2 * n + 1) := by
        simp [Finset.sum_range_succ]
      _ = n * n + (2 * n + 1) := by
        rw [ih]
      _ = (n + 1) * (n + 1) := by
        ring