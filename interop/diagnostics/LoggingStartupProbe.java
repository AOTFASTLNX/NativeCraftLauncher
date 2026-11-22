package fabricaot.probe;

/** Official logging initialization only; no game, Mod, account, or network code. */
public final class LoggingStartupProbe {
    public static void main(String[] args) {
        System.out.println("LOGGING_PROBE_BEGIN");
        if (!org.apache.logging.log4j.util.PropertiesUtil.getProperties()
                .getBooleanProperty("log4j2.disable.jmx"))
            throw new AssertionError("Launch with -Dlog4j2.disable.jmx=true");
        if (!javax.management.MBeanServerFactory.findMBeanServer(null).isEmpty())
            throw new AssertionError("Probe requires a fresh process without an MBeanServer");
        System.out.println("marker=" + com.mojang.logging.LogUtils.FATAL_MARKER);
        var logger = com.mojang.logging.LogUtils.getLogger();
        logger.info("isolated official logger");
        Object context = org.apache.logging.log4j.LogManager.getContext(false);
        if (!(context instanceof org.apache.logging.log4j.core.LoggerContext))
            throw new AssertionError("Log4j core provider unavailable: " + context.getClass().getName());
        var layout = org.apache.logging.log4j.core.layout.PatternLayout.newBuilder()
                .withPattern("%d{HH:mm:ss} [%thread/%level] %logger: %msg%n").build();
        var event = org.apache.logging.log4j.core.impl.Log4jLogEvent.newBuilder()
                .setLoggerName("logging-probe").setLevel(org.apache.logging.log4j.Level.ERROR)
                .setMessage(new org.apache.logging.log4j.message.SimpleMessage("formatted-message")).build();
        String formatted = layout.toSerializable(event);
        if (!formatted.contains("/ERROR] logging-probe: formatted-message")
                || formatted.contains("%") || !formatted.endsWith(System.lineSeparator()))
            throw new AssertionError("Log4j pattern plugins unavailable: " + formatted);
        System.out.print(formatted);
        org.apache.logging.log4j.core.jmx.Server.reregisterMBeansAfterReconfigure();
        if (!javax.management.MBeanServerFactory.findMBeanServer(null).isEmpty())
            throw new AssertionError("Logging initialized an MBeanServer despite disabled JMX");
        System.out.println("LOGGING_JMX_DISABLED_PASS");
        System.out.println("LOGGING_PROBE_PASS logger=" + logger.getName()
                + " context=" + context.getClass().getName());
    }
}
