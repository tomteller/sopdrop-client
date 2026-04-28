/**
 * Request logging middleware
 */

export function requestLogger(req, res, next) {
  const start = Date.now();

  // Log on response finish
  res.on('finish', () => {
    const duration = Date.now() - start;
    const status = res.statusCode;

    // Color based on status
    let statusColor = '\x1b[32m'; // Green
    if (status >= 400) statusColor = '\x1b[33m'; // Yellow
    if (status >= 500) statusColor = '\x1b[31m'; // Red

    console.log(
      `${statusColor}${status}\x1b[0m ${req.method} ${req.path} ${duration}ms`
    );
  });

  next();
}
