import math
import random
import sys
import tempfile
import wave
import struct
import io
import pygame

# -------- Game Config --------
WIDTH, HEIGHT = 960, 720
FPS = 60
ASTEROID_MIN_RADIUS = 16
ASTEROID_MAX_RADIUS = 60
ASTEROID_SPLIT_FACTOR = 0.55  # fraction of parent radius for children
ASTEROID_CHILD_VARIANCE = 0.15
ASTEROID_START_COUNT = 5
ASTEROID_SPEED_BASE = 1.2
LASER_SPEED = 9.0
LASER_COOLDOWN = 180  # ms
SHIP_THRUST = 0.18
SHIP_FRICTION = 0.992
SHIP_MAX_SPEED = 6.0
INVULN_TIME = 1500  # ms after death
LIVES = 3

COLOR_BG = (0, 0, 0)
COLOR_ASTEROID = (255, 255, 255)
COLOR_SHIP = (255, 230, 0)
COLOR_LASER = (0, 255, 0)
COLOR_UI = (220, 220, 220)

# -------- Simple Audio Synthesis (no assets required) --------
def synth_tone(filename, seconds=0.12, freq=880.0, volume=0.4, kind="square", fade_out=0.02):
    """Generate a simple WAV tone and write to filename."""
    framerate = 44100
    amp = int(32767 * volume)
    nframes = int(seconds * framerate)
    fo = int(fade_out * framerate)
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(framerate)
        for i in range(nframes):
            t = i / framerate
            # waveform
            if kind == "square":
                s = 1.0 if math.sin(2 * math.pi * freq * t) >= 0 else -1.0
            elif kind == "saw":
                # simple sawtooth
                s = 2.0 * ((t * freq) % 1.0) - 1.0
            elif kind == "sine":
                s = math.sin(2 * math.pi * freq * t)
            else:
                s = 0.0
            # simple fade out
            if i > nframes - fo:
                s *= (nframes - i) / fo
            wf.writeframesraw(struct.pack("<h", int(amp * s)))


def synth_noise_burst(filename, seconds=0.25, volume=0.45):
    """Generate a noise-burst WAV for 'explosion' feel."""
    framerate = 44100
    amp = int(32767 * volume)
    nframes = int(seconds * framerate)
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        for i in range(nframes):
            # gentle decay envelope
            env = max(0.0, 1.0 - (i / nframes))
            # colored-ish noise via averaging
            r = (random.random() + random.random() + random.random()) / 3.0  # [0,1]
            s = (r * 2.0 - 1.0) * env
            wf.writeframesraw(struct.pack("<h", int(amp * s)))


# -------- Utility --------
def wrap_position(x, y):
    return x % WIDTH, y % HEIGHT


def vec_from_angle(angle, magnitude=1.0):
    return (math.cos(angle) * magnitude, math.sin(angle) * magnitude)


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# -------- Game Objects --------
class Laser:
    def __init__(self, x, y, vx, vy):
        self.x, self.y = x, y
        self.vx, self.vy = vx, vy
        self.radius = 3
        self.alive = True
        self.ttl = 1200  # ms
        self.birth = pygame.time.get_ticks()

    def update(self, dt):
        self.x += self.vx
        self.y += self.vy
        self.x, self.y = wrap_position(self.x, self.y)
        if pygame.time.get_ticks() - self.birth > self.ttl:
            self.alive = False

    def draw(self, surf):
        pygame.draw.circle(surf, COLOR_LASER, (int(self.x), int(self.y)), self.radius)


class Asteroid:
    def __init__(self, x, y, radius, vx=None, vy=None):
        self.x, self.y = x, y
        self.radius = radius
        if vx is None or vy is None:
            ang = random.uniform(0, math.tau)
            speed = ASTEROID_SPEED_BASE + (ASTEROID_MAX_RADIUS - radius) * 0.02 + random.uniform(-0.3, 0.3)
            self.vx, self.vy = vec_from_angle(ang, speed)
        else:
            self.vx, self.vy = vx, vy

    def update(self, dt):
        self.x += self.vx
        self.y += self.vy
        self.x, self.y = wrap_position(self.x, self.y)

    def draw(self, surf):
        # Draw a slightly jagged polygon for a classic look
        points = []
        spikes = max(8, int(self.radius / 2))
        for i in range(spikes):
            ang = (i / spikes) * math.tau
            r_jitter = self.radius * (0.85 + random.random() * 0.3)
            px = self.x + math.cos(ang) * r_jitter
            py = self.y + math.sin(ang) * r_jitter
            points.append((px, py))
        pygame.draw.polygon(surf, COLOR_ASTEROID, points, width=2)

    def split(self):
        """Return list of child asteroids (or empty if too small)."""
        if self.radius <= ASTEROID_MIN_RADIUS:
            return []
        kids = []
        num_children = random.choice([2, 2, 3])  # bias toward 2
        for _ in range(num_children):
            new_r = max(
                ASTEROID_MIN_RADIUS,
                int(self.radius * (ASTEROID_SPLIT_FACTOR + random.uniform(-ASTEROID_CHILD_VARIANCE, ASTEROID_CHILD_VARIANCE)))
            )
            ang = random.uniform(0, math.tau)
            speed = (math.hypot(self.vx, self.vy) + 0.5) * (1.0 + random.uniform(-0.2, 0.35))
            vx, vy = vec_from_angle(ang, speed)
            kids.append(Asteroid(self.x, self.y, new_r, vx, vy))
        return kids


class Ship:
    def __init__(self):
        self.x, self.y = WIDTH / 2, HEIGHT / 2
        self.vx, self.vy = 0.0, 0.0
        self.angle = 0.0
        self.radius = 12
        self.alive = True
        self.invuln_until = 0

    def reset(self):
        self.__init__()
        self.invuln_until = pygame.time.get_ticks() + INVULN_TIME

    def update(self, keys, dt, mouse_pos):
        mx, my = mouse_pos
        self.angle = math.atan2(my - self.y, mx - self.x)

        thrusting = keys[pygame.K_w] or keys[pygame.K_UP]
        if thrusting:
            ax, ay = vec_from_angle(self.angle, SHIP_THRUST)
            self.vx += ax
            self.vy += ay
        if keys[pygame.K_s] or keys[pygame.K_DOWN]:
            ax, ay = vec_from_angle(self.angle + math.pi, SHIP_THRUST * 0.6)
            self.vx += ax
            self.vy += ay
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            ax, ay = vec_from_angle(self.angle - math.pi / 2, SHIP_THRUST * 0.6)
            self.vx += ax
            self.vy += ay
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            ax, ay = vec_from_angle(self.angle + math.pi / 2, SHIP_THRUST * 0.6)
            self.vx += ax
            self.vy += ay

        speed = math.hypot(self.vx, self.vy)
        if speed > SHIP_MAX_SPEED:
            scale = SHIP_MAX_SPEED / speed
            self.vx *= scale
            self.vy *= scale

        self.vx *= SHIP_FRICTION
        self.vy *= SHIP_FRICTION

        self.x += self.vx
        self.y += self.vy
        self.x, self.y = wrap_position(self.x, self.y)

    def draw(self, surf):
        # Ship triangle
        tip = (self.x + math.cos(self.angle) * (self.radius * 1.8),
               self.y + math.sin(self.angle) * (self.radius * 1.8))
        left = (self.x + math.cos(self.angle + 2.5) * self.radius,
                self.y + math.sin(self.angle + 2.5) * self.radius)
        right = (self.x + math.cos(self.angle - 2.5) * self.radius,
                 self.y + math.sin(self.angle - 2.5) * self.radius)

        if pygame.time.get_ticks() < self.invuln_until:
            # flicker when invulnerable
            if (pygame.time.get_ticks() // 120) % 2 == 0:
                pygame.draw.polygon(surf, COLOR_SHIP, [tip, left, right], width=2)
        else:
            pygame.draw.polygon(surf, COLOR_SHIP, [tip, left, right], width=2)

    def can_be_hit(self):
        return pygame.time.get_ticks() >= self.invuln_until


# -------- Game --------
class Game:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("Asteroid Destroyer")
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("consolas", 22)
        self.big_font = pygame.font.SysFont("consolas", 48)
        self.ship = Ship()
        self.lasers = []
        self.asteroids = []
        self.score = 0
        self.lives = LIVES
        self.last_shot = 0
        self.running = True
        self.paused = False
        self.game_over = False

        # Audio
        pygame.mixer.pre_init(44100, -16, 1, 512)
        pygame.mixer.init()
        self.snd_shoot, self.snd_explode, self.snd_thrust = self._make_sounds()

        self._spawn_wave(ASTEROID_START_COUNT)

    def _tmp_wav(self):
        return tempfile.NamedTemporaryFile(delete=False, suffix=".wav")

    def _make_sounds(self):
        # Create three temporary wav files and load as pygame sounds
        shoot_file = self._tmp_wav()
        explode_file = self._tmp_wav()
        thrust_file = self._tmp_wav()
        shoot_file.close()
        explode_file.close()
        thrust_file.close()

        synth_tone(shoot_file.name, seconds=0.09, freq=920, volume=0.5, kind="square", fade_out=0.02)
        synth_noise_burst(explode_file.name, seconds=0.3, volume=0.6)
        synth_tone(thrust_file.name, seconds=0.2, freq=160, volume=0.35, kind="saw", fade_out=0.05)

        snd_shoot = pygame.mixer.Sound(shoot_file.name)
        snd_explode = pygame.mixer.Sound(explode_file.name)
        snd_thrust = pygame.mixer.Sound(thrust_file.name)
        snd_thrust.set_volume(0.4)
        return snd_shoot, snd_explode, snd_thrust

    def _spawn_wave(self, count):
        self.asteroids.clear()
        margin = 80
        for _ in range(count):
            # spawn away from the ship
            while True:
                x = random.randrange(margin, WIDTH - margin)
                y = random.randrange(margin, HEIGHT - margin)
                if math.hypot(x - self.ship.x, y - self.ship.y) > 200:
                    break
            r = random.randint(ASTEROID_MAX_RADIUS - 15, ASTEROID_MAX_RADIUS)
            self.asteroids.append(Asteroid(x, y, r))

    def shoot(self):
        now = pygame.time.get_ticks()
        if now - self.last_shot < LASER_COOLDOWN:
            return
        self.last_shot = now
        vx, vy = vec_from_angle(self.ship.angle, LASER_SPEED)
        lx = self.ship.x + math.cos(self.ship.angle) * (self.ship.radius * 1.8)
        ly = self.ship.y + math.sin(self.ship.angle) * (self.ship.radius * 1.8)
        self.lasers.append(Laser(lx, ly, vx, vy))
        if self.snd_shoot:
            self.snd_shoot.play()

    def update(self, dt):
        if self.game_over or self.paused:
            return

        keys = pygame.key.get_pressed()
        mouse_pos = pygame.mouse.get_pos()

        # play quiet looping thrust when moving forward
        if keys[pygame.K_w] or keys[pygame.K_UP]:
            if not pygame.mixer.get_busy():
                self.snd_thrust.play()

        self.ship.update(keys, dt, mouse_pos)

        # lasers
        for l in self.lasers:
            l.update(dt)
        self.lasers = [l for l in self.lasers if l.alive]

        # asteroids
        for a in self.asteroids:
            a.update(dt)

        # collisions: lasers vs asteroids
        new_asteroids = []
        for a in self.asteroids:
            hit = False
            for l in self.lasers:
                if (a.x - l.x) ** 2 + (a.y - l.y) ** 2 <= (a.radius + l.radius) ** 2:
                    l.alive = False
                    hit = True
                    break
            if hit:
                self.score += max(1, (ASTEROID_MAX_RADIUS - a.radius) // 5)
                kids = a.split()
                if kids:
                    new_asteroids.extend(kids)
                if self.snd_explode:
                    self.snd_explode.play()
            else:
                new_asteroids.append(a)
        self.asteroids = new_asteroids

        # collisions: ship vs asteroids
        if self.ship.can_be_hit():
            for a in self.asteroids:
                if (a.x - self.ship.x) ** 2 + (a.y - self.ship.y) ** 2 <= (a.radius + self.ship.radius) ** 2:
                    self.lives -= 1
                    self.ship.reset()
                    if self.snd_explode:
                        self.snd_explode.play()
                    if self.lives <= 0:
                        self.game_over = True
                    break

        # next wave?
        if not self.asteroids and not self.game_over:
            # ramp difficulty by increasing count a bit
            next_count = min(12, 5 + (self.score // 15))
            self._spawn_wave(next_count)

    def draw(self):
        self.screen.fill(COLOR_BG)

        # draw objects
        for a in self.asteroids:
            a.draw(self.screen)
        for l in self.lasers:
            l.draw(self.screen)
        self.ship.draw(self.screen)

        # UI
        score_surf = self.font.render(f"Score: {self.score}", True, COLOR_UI)
        lives_surf = self.font.render(f"Lives: {self.lives}", True, COLOR_UI)
        self.screen.blit(score_surf, (10, 10))
        self.screen.blit(lives_surf, (10, 38))

        if self.paused:
            p = self.big_font.render("PAUSED  (P to resume)", True, (180, 180, 255))
            self.screen.blit(p, (WIDTH // 2 - p.get_width() // 2, HEIGHT // 2 - p.get_height() // 2))

        if self.game_over:
            go = self.big_font.render("GAME OVER", True, (255, 120, 120))
            tip = self.font.render("Press R to restart or ESC to quit", True, (230, 230, 230))
            self.screen.blit(go, (WIDTH // 2 - go.get_width() // 2, HEIGHT // 2 - go.get_height()))
            self.screen.blit(tip, (WIDTH // 2 - tip.get_width() // 2, HEIGHT // 2 + 8))

        pygame.display.flip()

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.running = False
                elif event.key == pygame.K_p:
                    self.paused = not self.paused
                elif event.key == pygame.K_r and self.game_over:
                    self.__init__()  # quick reset
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1 and not self.paused and not self.game_over:
                    self.shoot()

    def run(self):
        while self.running:
            dt = self.clock.tick(FPS)
            self.handle_events()
            self.update(dt)
            self.draw()
        pygame.quit()


# -------- Main --------
if __name__ == "__main__":
    try:
        Game().run()
    except Exception as e:
        pygame.quit()
        print("Error:", e)
        sys.exit(1)
