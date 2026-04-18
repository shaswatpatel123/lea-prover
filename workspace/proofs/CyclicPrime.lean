import Mathlib

theorem finite_group_of_prime_order_is_cyclic {G : Type*} [Group G] [Finite G] {p : ℕ} (hp : p.Prime) (h : Nat.card G = p) : IsCyclic G := by
  haveI : Fact p.Prime := ⟨hp⟩
  exact isCyclic_of_prime_card h
