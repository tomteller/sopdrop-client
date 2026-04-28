/**
 * Feedback routes - Bug reports and feature requests
 */

import { Router } from 'express';
import { query } from '../models/db.js';
import { optionalAuth, authenticate } from '../middleware/auth.js';
import { ValidationError } from '../middleware/errorHandler.js';

const router = Router();

/**
 * POST /feedback
 * Submit feedback (bug report, feature request, etc.)
 */
router.post('/', optionalAuth, async (req, res, next) => {
  try {
    const { type, title, description, email } = req.body;

    // Validation
    if (!title || !title.trim()) {
      throw new ValidationError('Title is required');
    }

    if (!['bug', 'feature', 'other'].includes(type)) {
      throw new ValidationError('Invalid feedback type');
    }

    if (title.length > 200) {
      throw new ValidationError('Title must be 200 characters or less');
    }

    if (description && description.length > 5000) {
      throw new ValidationError('Description must be 5000 characters or less');
    }

    // Insert feedback
    const result = await query(`
      INSERT INTO feedback (type, title, description, email, user_id, user_agent, created_at)
      VALUES ($1, $2, $3, $4, $5, $6, NOW())
      RETURNING id, type, title, created_at
    `, [
      type,
      title.trim(),
      description?.trim() || null,
      email?.trim() || null,
      req.user?.id || null,
      req.headers['user-agent'] || null,
    ]);

    const feedback = result.rows[0];

    res.status(201).json({
      success: true,
      feedback: {
        id: feedback.id,
        type: feedback.type,
        title: feedback.title,
        createdAt: feedback.created_at,
      },
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /feedback
 * List all feedback (admin only)
 */
router.get('/', authenticate, async (req, res, next) => {
  try {
    // Check if admin
    if (!req.user.isAdmin) {
      throw new ValidationError('Admin access required');
    }

    const { type, status, limit = 50, offset = 0 } = req.query;

    let whereClause = '';
    const params = [];
    let paramIndex = 1;

    if (type) {
      whereClause += ` AND f.type = $${paramIndex++}`;
      params.push(type);
    }

    if (status) {
      whereClause += ` AND f.status = $${paramIndex++}`;
      params.push(status);
    }

    params.push(parseInt(limit), parseInt(offset));

    const result = await query(`
      SELECT
        f.*,
        u.username,
        u.email as user_email
      FROM feedback f
      LEFT JOIN users u ON f.user_id = u.id
      WHERE 1=1 ${whereClause}
      ORDER BY f.created_at DESC
      LIMIT $${paramIndex++} OFFSET $${paramIndex}
    `, params);

    const countResult = await query(`
      SELECT COUNT(*) as total FROM feedback f WHERE 1=1 ${whereClause}
    `, params.slice(0, -2));

    res.json({
      feedback: result.rows.map(f => ({
        id: f.id,
        type: f.type,
        title: f.title,
        description: f.description,
        status: f.status,
        email: f.email || f.user_email,
        username: f.username,
        userAgent: f.user_agent,
        createdAt: f.created_at,
      })),
      total: parseInt(countResult.rows[0].total),
    });
  } catch (error) {
    next(error);
  }
});

/**
 * PATCH /feedback/:id
 * Update feedback status (admin only)
 */
router.patch('/:id', authenticate, async (req, res, next) => {
  try {
    if (!req.user.isAdmin) {
      throw new ValidationError('Admin access required');
    }

    const { status, notes } = req.body;

    if (status && !['new', 'reviewing', 'planned', 'in-progress', 'done', 'wont-fix'].includes(status)) {
      throw new ValidationError('Invalid status');
    }

    const result = await query(`
      UPDATE feedback
      SET
        status = COALESCE($1, status),
        admin_notes = COALESCE($2, admin_notes),
        updated_at = NOW()
      WHERE id = $3
      RETURNING *
    `, [status, notes, req.params.id]);

    if (result.rows.length === 0) {
      throw new ValidationError('Feedback not found');
    }

    res.json({ success: true, feedback: result.rows[0] });
  } catch (error) {
    next(error);
  }
});

export default router;
