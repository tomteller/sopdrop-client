/**
 * Abuse reporting routes
 * For reporting malware, copyright violations, impersonation, spam
 */

import { Router } from 'express';
import { query } from '../models/db.js';
import { optionalAuth, authenticate, requireMod } from '../middleware/auth.js';
import { ValidationError, NotFoundError, ForbiddenError } from '../middleware/errorHandler.js';
import { logAuditEvent, generalLimiter, sanitizePlainText } from '../middleware/security.js';

const router = Router();

// Valid report reasons
const VALID_REASONS = ['malware', 'copyright', 'impersonation', 'spam', 'other'];
const VALID_TARGET_TYPES = ['asset', 'user'];

/**
 * POST /reports
 * Submit an abuse report
 */
router.post('/', optionalAuth, generalLimiter, async (req, res, next) => {
  try {
    const { targetType, targetId, reason, details, contactEmail } = req.body;

    // Validation
    if (!targetType || !VALID_TARGET_TYPES.includes(targetType)) {
      throw new ValidationError(`targetType must be one of: ${VALID_TARGET_TYPES.join(', ')}`);
    }

    if (!targetId) {
      throw new ValidationError('targetId is required');
    }

    if (!reason || !VALID_REASONS.includes(reason)) {
      throw new ValidationError(`reason must be one of: ${VALID_REASONS.join(', ')}`);
    }

    // Require contact email for anonymous reports
    if (!req.user && !contactEmail) {
      throw new ValidationError('contactEmail is required for anonymous reports');
    }

    // Validate email format if provided
    if (contactEmail && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(contactEmail)) {
      throw new ValidationError('Invalid email format');
    }

    // Verify target exists
    if (targetType === 'asset') {
      // Asset slug format: owner/asset-name
      const [owner, assetSlug] = targetId.split('/');
      if (!owner || !assetSlug) {
        throw new ValidationError('Invalid asset slug format (expected: owner/asset-name)');
      }

      const assetResult = await query(`
        SELECT a.id FROM assets a
        JOIN users u ON a.owner_id = u.id
        WHERE u.username = $1 AND a.slug = $2
      `, [owner.toLowerCase(), assetSlug.toLowerCase()]);

      if (assetResult.rows.length === 0) {
        throw new NotFoundError('Asset not found');
      }
    } else if (targetType === 'user') {
      const userResult = await query(`
        SELECT id FROM users WHERE username = $1
      `, [targetId.toLowerCase()]);

      if (userResult.rows.length === 0) {
        throw new NotFoundError('User not found');
      }
    }

    // Check for duplicate recent reports from same IP/user
    const duplicateCheck = await query(`
      SELECT id FROM abuse_reports
      WHERE target_type = $1
        AND target_id = $2
        AND (reporter_id = $3 OR reporter_ip = $4)
        AND created_at > NOW() - INTERVAL '24 hours'
        AND status = 'pending'
      LIMIT 1
    `, [targetType, targetId, req.user?.id || null, req.ip]);

    if (duplicateCheck.rows.length > 0) {
      throw new ValidationError('You have already reported this recently. Our team is reviewing it.');
    }

    // Insert report
    const result = await query(`
      INSERT INTO abuse_reports (target_type, target_id, reason, details, reporter_id, reporter_email, reporter_ip)
      VALUES ($1, $2, $3, $4, $5, $6, $7)
      RETURNING id, created_at
    `, [
      targetType,
      targetId,
      reason,
      details ? sanitizePlainText(details, 5000) : null,
      req.user?.id || null,
      contactEmail || req.user?.email || null,
      req.ip,
    ]);

    const report = result.rows[0];

    // Log the report
    logAuditEvent('report_created', 'ABUSE', req, {
      targetType,
      targetId,
      reason,
      reportId: report.id,
    });

    res.status(201).json({
      success: true,
      reportId: report.id,
      message: 'Report submitted. Our team will review it within 24-48 hours.',
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /reports (Moderator+)
 * List abuse reports
 */
router.get('/', authenticate, requireMod, async (req, res, next) => {
  try {

    const { status = 'pending', limit = 50, offset = 0 } = req.query;

    const result = await query(`
      SELECT
        r.*,
        reporter.username as reporter_username
      FROM abuse_reports r
      LEFT JOIN users reporter ON r.reporter_id = reporter.id
      WHERE ($1::text IS NULL OR r.status = $1)
      ORDER BY r.created_at DESC
      LIMIT $2 OFFSET $3
    `, [status === 'all' ? null : status, Math.min(parseInt(limit) || 50, 100), parseInt(offset) || 0]);

    const countResult = await query(`
      SELECT COUNT(*) as total
      FROM abuse_reports
      WHERE ($1::text IS NULL OR status = $1)
    `, [status === 'all' ? null : status]);

    res.json({
      reports: result.rows,
      total: parseInt(countResult.rows[0].total),
      limit: parseInt(limit),
      offset: parseInt(offset),
    });
  } catch (error) {
    next(error);
  }
});

/**
 * PUT /reports/:id (Moderator+)
 * Resolve an abuse report
 */
router.put('/:id', authenticate, requireMod, async (req, res, next) => {
  try {

    const { status, resolutionNotes } = req.body;

    if (!status || !['resolved', 'dismissed', 'actioned'].includes(status)) {
      throw new ValidationError('status must be: resolved, dismissed, or actioned');
    }

    const sanitizedNotes = resolutionNotes ? sanitizePlainText(resolutionNotes, 2000) : null;

    const result = await query(`
      UPDATE abuse_reports
      SET status = $1,
          resolution_notes = $2,
          resolved_by = $3,
          resolved_at = NOW()
      WHERE id = $4
      RETURNING *
    `, [status, sanitizedNotes, req.user.id, req.params.id]);

    if (result.rows.length === 0) {
      throw new NotFoundError('Report not found');
    }

    const report = result.rows[0];

    // Log admin action
    logAuditEvent('report_resolved', 'ADMIN', req, {
      targetType: 'abuse_report',
      targetId: req.params.id,
      status,
      originalTarget: `${report.target_type}:${report.target_id}`,
    });

    res.json({
      success: true,
      report,
    });
  } catch (error) {
    next(error);
  }
});

export default router;
