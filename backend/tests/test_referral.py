"""Referral summary parsing: hoisted $R[n] refs, no-referral fallback, status handling."""
from app.referral import ReferralSummary, parse_referral_summary


def test_no_referral_returns_empty_summary():
    summary = parse_referral_summary("<div>no referral data here</div>")
    assert isinstance(summary, ReferralSummary)
    assert summary.has_referral is False
    assert summary.reward_amount == 0.0
    assert summary.rewards == []


def test_flat_rewards_array():
    html = '''
    obj = { referralCode: "QH-1", hasReferral: !0, rewardAmount: 500,
      rewards: [ {id:"rw_1", source:"referral", status:"available", email:"a@b.c",
      amount:500, timeCreated:"2026-07-01T00:00:00Z"} ] }
    '''
    summary = parse_referral_summary(html)
    assert summary.referral_code == "QH-1"
    assert summary.has_referral is True
    assert summary.reward_amount == 5.0
    assert len(summary.rewards) == 1
    assert summary.rewards[0].status == "available"
    assert summary.rewards[0].amount == 5.0


def test_inline_resource_rewards():
    html = '''
    obj = { referralCode: "QH-2", hasReferral: !0, rewardAmount: 800,
      rewards: $R[7] = [ {id:"rw_2", source:"referral", status:"pending", email:"a@b.c",
      amount:800, timeCreated:"2026-07-01T00:00:00Z"} ] }
    '''
    summary = parse_referral_summary(html)
    assert summary.reward_amount == 8.0
    assert len(summary.rewards) == 1
    assert summary.rewards[0].status == "pending"


def test_hoisted_scalar_refs():
    """Field values referenced as $R[n] whose definitions appear after the object."""
    html = '''
    const obj = { referralCode: "QH-3", hasReferral: !0, rewardAmount: $R[9],
      rewards: [ {id:"rw_3", source:"referral", status:"available", email:"a@b.c",
      amount: $R[3], timeCreated:"2026-07-01T00:00:00Z"} ] }
    $R[9] = 500
    $R[3] = 300
    '''
    summary = parse_referral_summary(html)
    # reward_amount = sum of rewards; the single reward is 300 cents = $3.00.
    # (The page's rewardAmount=500 is the per-invite unit price, not the total.)
    assert summary.reward_amount == 3.0
    assert len(summary.rewards) == 1
    assert summary.rewards[0].amount == 3.0


def test_hoisted_rewards_array_multiline():
    html = '''
    const obj2 = { referralCode: "QH-4", hasReferral: !0, rewardAmount: 1200,
      rewards: $R[5] }
    $R[5] = [
      {id:"rw_5", source:"referral", status:"available", email:"x@y.z", amount:1200,
      timeCreated:"2026-07-01T00:00:00Z"}
    ]
    '''
    summary = parse_referral_summary(html)
    assert summary.reward_amount == 12.0
    assert len(summary.rewards) == 1
    assert summary.rewards[0].amount == 12.0
    assert summary.rewards[0].status == "available"


def test_has_referral_without_rewards_field():
    """A referral block with code but missing rewards is still recognized."""
    html = '''
    obj = { referralCode: "QH-6", hasReferral: !0 }
    '''
    summary = parse_referral_summary(html)
    assert summary.referral_code == "QH-6"
    assert summary.rewards == []


def test_empty_rewards_means_zero_total():
    """rewards=[] but rewardAmount present -> total must be $0.00 (no bonus actually received)."""
    html = '''
    obj = { referralCode: "1GNQQN4E9X", hasReferral: !0, rewardAmount: 500,
      rewards: [] }
    '''
    summary = parse_referral_summary(html)
    assert summary.referral_code == "1GNQQN4E9X"
    assert summary.has_referral is True
    assert summary.reward_amount == 0.0
    assert summary.rewards == []


def test_reward_amount_is_sum_of_rewards_not_unit_price():
    """rewardAmount is per-invite unit price; total must be the rewards sum."""
    html = '''
    obj = { referralCode: "QH-7", hasReferral: !0, rewardAmount: 200,
      rewards: [
        {id:"rw_a", source:"referral", status:"available", email:"a@b.c", amount:200,
         timeCreated:"2026-07-01T00:00:00Z"},
        {id:"rw_b", source:"referral", status:"applied", email:"b@c.d", amount:300,
         timeCreated:"2026-07-01T00:00:00Z"}
      ] }
    '''
    summary = parse_referral_summary(html)
    assert len(summary.rewards) == 2
    # 2.00 + 3.00 = 5.00, NOT the unit price 2.00.
    assert summary.reward_amount == 5.0
