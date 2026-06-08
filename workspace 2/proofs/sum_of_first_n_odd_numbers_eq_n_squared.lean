import Mathlib.Data.Nat.Basic
import Mathlib.Algebra.BigOperators.Basic
import Mathlib.Tactic

open BigOperators

-- Inductive proof for the sum of the first n odd numbers equals n squared
theorem sum_of_first_n_odd_numbers_eq_n_squared (n : ℕ) :
  ∑ i in Finset.range n, (2 * i + 1) = n * n := by
  induction n with k hk
  -- Base case
  · simp
  -- Inductive step
  · calc
      ∑ i in Finset.range (k + 1), (2 * i + 1)
          = ∑ i in Finset.range k, (2 * i + 1) + (2 * k + 1) := by rw[Finset.sum_range_succ]
      _ = k * k + (2 * k + 1) := by rw [hk]
      _ = (k + 1) * (k + 1) := by ring