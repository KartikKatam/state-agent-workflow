# Robotics-CV: Simulation & Sim-to-Real Testing

Deep-dive for testing sim-to-real transfer, domain randomization, sensor noise models, and multi-agent simulation.

## Sim-to-Real Gap Thresholds

| Metric | Acceptable Gap | Intervention Needed | Source |
|--------|---------------|---------------------|--------|
| mAP drop (sim-trained → real) | < 15% relative | Domain adaptation at > 15% | Tremblay et al. 2018 |
| mAP drop (sim+real mixed → real) | < 5% relative | Standard mitigation via mixed training | Industrial studies |
| RL policy transfer success rate | > 80% of sim performance | Fine-tuning if < 80% | NVIDIA Isaac Lab |
| Trajectory error increase | < 2× sim error | Physics model calibration needed | AirSim/Colosseum |
| Object localization accuracy | < 1.5 cm error | Domain randomization effective | Tobin et al. 2017 |

## Sensor Noise Validation

```python
# Source: Gazebo sensor noise model (additive Gaussian)

def test_simulated_camera_noise_matches_real():
    """Sim camera noise should approximate real sensor characteristics."""
    sim_frames = capture_static_scene(sim_camera, n=100)
    real_frames = capture_static_scene(real_camera, n=100)
    sim_variance = np.var(sim_frames, axis=0).mean()
    real_variance = np.var(real_frames, axis=0).mean()
    ratio = sim_variance / real_variance
    # Noise should be within 2× of real sensor
    assert 0.5 < ratio < 2.0, (
        f"Sim noise variance {sim_variance:.2f} vs real {real_variance:.2f} "
        f"(ratio {ratio:.2f}). Adjust Gaussian noise stddev in sensor SDF."
    )

def test_simulated_imu_noise_characteristics():
    """IMU noise model must include both white noise and bias.
    Source: Gazebo IMU config — rate_noise, rate_bias, accel_noise, accel_bias."""
    imu_data = collect_static_imu(duration_s=10.0)
    gyro_noise = compute_allan_variance(imu_data.gyro, tau=1.0)
    accel_noise = compute_allan_variance(imu_data.accel, tau=1.0)
    # Typical MEMS IMU specs (MPU-6050 class)
    assert gyro_noise < 0.05, "Gyro noise too high for MEMS IMU model"
    assert accel_noise < 0.02, "Accel noise too high"
```

## Domain Randomization Sufficiency

```python
# Source: Tobin et al. 2017 — significant drop with < 1000 textures
#         Tremblay et al. 2018 — camera azimuth 0-360°, 1-12 lights

def test_domain_randomization_diversity():
    """DR config must meet minimum diversity thresholds for generalization."""
    dr_config = load_dr_config()
    assert dr_config.num_textures >= 1000, (
        f"Only {dr_config.num_textures} textures (Tobin 2017: < 1000 causes drop)"
    )
    assert dr_config.num_lights_range == (1, 12), "Need 1-12 random lights"
    assert dr_config.camera_azimuth_range == (0, 360)

def test_synthetic_data_does_not_hurt_real_performance():
    """Adding synthetic data should improve or not hurt real eval."""
    model_real = train_detector(real_data)
    model_mixed = train_detector(real_data + synthetic_data)
    ap_real = evaluate_on_real(model_real)
    ap_mixed = evaluate_on_real(model_mixed)
    assert ap_mixed >= ap_real * 0.95, (
        f"Synthetic data hurting: mixed AP {ap_mixed:.3f} < real-only {ap_real:.3f}"
    )
```

## Physics and Environment Validation

```python
def test_physics_consistency_free_fall():
    """Simulated gravity should match expected 9.81 m/s²."""
    obj = spawn_object(mass=1.0, position=(0, 0, 10.0))
    sim.step(dt=0.001, steps=1000)  # 1 second
    expected_v = 9.81  # v = g*t
    assert abs(obj.velocity.z - expected_v) < 0.1
```

## Multi-Agent Safety Invariants

```python
def test_collision_avoidance_invariant(sim_env):
    """No two agents should violate minimum separation at any timestep."""
    min_separation = 2.0  # meters — FAA minimum for small UAS
    for t in range(sim_env.num_timesteps):
        positions = sim_env.get_all_positions(t)
        for i in range(len(positions)):
            for j in range(i + 1, len(positions)):
                dist = np.linalg.norm(positions[i] - positions[j])
                assert dist > min_separation, f"Collision: agents {i},{j} at {dist:.2f}m"

def test_geofence_invariant(sim_env):
    """All agents must remain within operational boundary at all times."""
    for t in range(sim_env.num_timesteps):
        for agent_id, pos in enumerate(sim_env.get_all_positions(t)):
            assert sim_env.geofence.contains(pos), f"Agent {agent_id} outside geofence"

def test_communication_loss_safe_behavior(sim_env):
    """Agent must enter safe mode when comms lost for > 5 seconds."""
    sim_env.disable_comms(sim_env.agents[0], duration_s=10.0)
    sim_env.step(steps=1000)
    assert sim_env.agents[0].mode in ("LOITER", "RTL"), (
        f"Agent in {sim_env.agents[0].mode} after 10s comms loss"
    )
```

## Failure Modes

| Failure Mode | Detection | Source |
|---|---|---|
| **Lighting domain gap** | Compare pixel intensity mean/std between sim and real datasets | DR literature, Isaac Sim |
| **Texture unrealism** | Train on real, compare vs sim-trained — gap > 15% = texture problem | Tobin 2017 |
| **Physics fidelity (contact)** | Grasping success in sim vs real; > 2× difference = contact model issue | NVIDIA Isaac Lab |
| **Sensor noise mismatch** | Allan variance ratio outside [0.5, 2.0] needs recalibration | Gazebo sensor noise docs |
| **Motion blur absence** | Sim renders sharp; real has blur. Test model on blurred synthetic frames | Common sim limitation |
| **Missing environmental effects** | Rain, dust, fog absent in sim. Test with augmented data | AirSim weather API |
| **Sim-specific artifacts** | Aliasing, z-fighting leak into training data. Visual inspection needed | Game engine artifacts |
