// SPDX-FileCopyrightText: 2026 czicompress contributors
// SPDX-License-Identifier: GPL-3.0-or-later

namespace netczicompress.Models;

/// <summary>
/// Evidence about the operation actually performed on one file.
/// </summary>
public record ProcessingDetails(
    string FileFormat,
    string Operation,
    string Method,
    string Action,
    string Verification,
    bool? ContainerByteIdentical = null,
    string? InputSha256 = null,
    string? OutputSha256 = null,
    string? OutputFile = null);
