import Mathlib.Data.Nat.Basic
import Mathlib.Algebra.BigOperators.Basic
import Mathlib.Tactic

open BigOperators

theorem sum_of_first_n_odd_numbers_eq_n_squared (n : ℕ) :
  ∑ i in Finset.range n, (2 * i + 1) = n * n := by sorry
