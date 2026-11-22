package chatfixture;

/** Counters plus a release gate standing in for the unfinished services-key future. */
public final class Counts {
    public static int keys;
    public static int validators;
    public static int sessions;
    public static int validations;
    public static int validationErrors;
    public static int clearCalls;
    public static int setCalls;
    public static int validatorCalls;
    public static boolean lastEnforced;
    public static boolean failValidation;

    private static boolean keysOpen;

    private Counts() {}

    public static synchronized void reset() {
        keys = validators = sessions = validations = validationErrors = 0;
        clearCalls = setCalls = validatorCalls = 0;
        lastEnforced = false;
        failValidation = false;
        keysOpen = false;
    }

    /** Blocks like Minecraft.canValidateProfileKeys() joining an unfinished key future. */
    public static synchronized void awaitKeys() {
        long deadline = System.nanoTime() + 5_000_000_000L;
        while (!keysOpen) {
            long remaining = deadline - System.nanoTime();
            if (remaining <= 0) throw new AssertionError("services-key gate timed out");
            try {
                Counts.class.wait(Math.max(1L, remaining / 1_000_000L));
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
                throw new AssertionError(error);
            }
        }
    }

    public static synchronized void openKeys() {
        keysOpen = true;
        Counts.class.notifyAll();
    }
}
