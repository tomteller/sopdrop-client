/**
 * Error handling middleware
 */

export class AppError extends Error {
  constructor(message, statusCode = 500, code = 'INTERNAL_ERROR') {
    super(message);
    this.statusCode = statusCode;
    this.code = code;
    this.isOperational = true;

    Error.captureStackTrace(this, this.constructor);
  }
}

export class NotFoundError extends AppError {
  constructor(message = 'Resource not found') {
    super(message, 404, 'NOT_FOUND');
  }
}

export class AuthError extends AppError {
  constructor(message = 'Authentication required') {
    super(message, 401, 'AUTH_REQUIRED');
  }
}

export class ForbiddenError extends AppError {
  constructor(message = 'Access denied') {
    super(message, 403, 'FORBIDDEN');
  }
}

export class ValidationError extends AppError {
  constructor(message = 'Invalid input') {
    super(message, 400, 'VALIDATION_ERROR');
  }
}

export class ConflictError extends AppError {
  constructor(message = 'Resource already exists') {
    super(message, 409, 'CONFLICT');
  }
}

/**
 * Global error handler middleware
 */
export function errorHandler(err, req, res, next) {
  // Log error
  if (process.env.NODE_ENV !== 'test') {
    console.error('Error:', err.message);
    if (process.env.NODE_ENV === 'development') {
      console.error(err.stack);
    }
  }

  // Handle known errors
  if (err.isOperational) {
    return res.status(err.statusCode).json({
      error: err.message,
      code: err.code,
    });
  }

  // Handle Postgres errors
  if (err.code === '23505') {
    // Unique violation
    return res.status(409).json({
      error: 'Resource already exists',
      code: 'CONFLICT',
    });
  }

  if (err.code === '23503') {
    // Foreign key violation
    return res.status(400).json({
      error: 'Referenced resource does not exist',
      code: 'INVALID_REFERENCE',
    });
  }

  // Unknown error
  res.status(500).json({
    error: process.env.NODE_ENV === 'production'
      ? 'Internal server error'
      : err.message,
    code: 'INTERNAL_ERROR',
  });
}
