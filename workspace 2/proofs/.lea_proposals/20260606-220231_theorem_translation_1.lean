import Mathlib

open Finset

theorem sum_of_first_n_odd_numbers (n : ℕ) : (Finset.range n).sum (fun i => 2 * i + 1) = n^2 := by sorry
