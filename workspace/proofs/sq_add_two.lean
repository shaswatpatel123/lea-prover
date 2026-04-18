import Mathlib

set_option linter.unusedVariables false

lemma zmod_sq_add_two_neq_zero (m : ZMod 4) : m^2 + 2 ≠ 0 := by
  revert m
  decide

theorem not_dvd_sq_add_two (n : ℕ) (hn : 0 < n) : ¬ 4 ∣ n^2 + 2 := by
  intro h
  have h1 : ((n^2 + 2 : ℕ) : ZMod 4) = 0 := by
    rcases h with ⟨k, hk⟩
    calc ((n^2 + 2 : ℕ) : ZMod 4) = ((4 * k : ℕ) : ZMod 4) := by rw [hk]
      _ = (4 : ZMod 4) * (k : ZMod 4) := by push_cast; rfl
      _ = 0 * (k : ZMod 4) := by rfl
      _ = 0 := by ring
  have h2 : (n : ZMod 4)^2 + 2 = 0 := by
    calc (n : ZMod 4)^2 + 2 = ((n^2 + 2 : ℕ) : ZMod 4) := by push_cast; rfl
      _ = 0 := h1
  exact zmod_sq_add_two_neq_zero (n : ZMod 4) h2
