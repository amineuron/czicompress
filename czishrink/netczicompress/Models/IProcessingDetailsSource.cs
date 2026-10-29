// SPDX-FileCopyrightText: 2026 czicompress contributors
// SPDX-License-Identifier: GPL-3.0-or-later

namespace netczicompress.Models;

/// <summary>
/// Optional per-file reporting contract for processors.
/// </summary>
public interface IProcessingDetailsSource
{
    ProcessingDetails? LastDetails { get; }
}
