import Mathlib.Data.Nat.Basic
import Mathlib.Tactic

open Nat

theorem sum_of_first_n_odd_numbers (n : ℕ) : (finset.range n).sum (λ k, 2 * k + 1) = n * n := by sorry
