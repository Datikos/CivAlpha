package com.civalpha.sec;

/** Minimal blocking limiter: at most `permitsPerSecond` acquisitions per second across threads. */
public final class RateLimiter {
    private final long intervalNanos;
    private long next = 0;

    public RateLimiter(double permitsPerSecond) {
        if (permitsPerSecond <= 0 || permitsPerSecond > 10) {
            throw new IllegalArgumentException("SEC fair access allows at most 10 requests/second; got " + permitsPerSecond);
        }
        this.intervalNanos = (long) (1_000_000_000L / permitsPerSecond);
    }

    public void acquire() {
        long wait;
        synchronized (this) {
            long now = System.nanoTime();
            long slot = Math.max(now, next);
            next = slot + intervalNanos;
            wait = slot - now;
        }
        if (wait > 0) {
            try {
                Thread.sleep(wait / 1_000_000, (int) (wait % 1_000_000));
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException("interrupted while rate limiting", e);
            }
        }
    }
}
