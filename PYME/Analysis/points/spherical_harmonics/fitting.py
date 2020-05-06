"""
Estimate spherical harmonics from a point data set
Initial fitting/conversions ripped 100% from David Baddeley / scipy
"""
import numpy as np
from scipy.special import sph_harm
from scipy import linalg
from PYME.Analysis.points import coordinate_tools
from scipy import optimize
import logging

logger = logging.getLogger(__name__)


def r_sph_harm(m, n, azimuth, zenith):
    """
    return real valued spherical harmonics. Uses the convention that m > 0 corresponds to the cosine terms, m < zero the
    sine terms

    Parameters
    ----------
    m : int

    n : int

    azimuth : ndarray
        the azimuth angle in [0, 2pi]
    zenith : ndarray
        the elevation in [0, pi]

    Returns
    -------

    """
    if m > 0:
        return (1. / np.sqrt(2) * (-1) ** m) * sph_harm(m, n, azimuth, zenith).real
    elif m == 0:
        return sph_harm(m, n, azimuth, zenith).real
    else:
        return (1. / np.sqrt(2) * (-1) ** m) * sph_harm(m, n, azimuth, zenith).imag


def sphere_expansion(x, y, z, mmax=3):
    """
    Project coordinates onto spherical harmonics

    Parameters
    ----------
    x : ndarray
        x coordinates
    y : ndarray
        y coordinates
    z : ndarray
        z coordinates
    mmax : int
        Maximum order to calculate to

    Returns
    -------

    modes : list of tuples
        a list of the (m, n) modes projected onto
    c : ndarray
        the mode coefficients


    """

    azimuth, zenith, r = coordinate_tools.cartesian_to_spherical(x, y, z)

    A = []
    modes = []
    for m in range(mmax + 1):
        for n in range(-m, m + 1):
            sp_mode = r_sph_harm(n, m, azimuth, zenith)
            A.append(sp_mode)

            modes.append((m, n))

    A = np.vstack(A)

    c = linalg.lstsq(A.T, r)[0]

    return modes, c


def sphere_expansion_clean(x, y, z, mmax=3, nIters=2, tol_init=0.3):
    """
    Project coordinates onto spherical harmonics

    Parameters
    ----------
    x : ndarray
        x coordinates
    y : ndarray
        y coordinates
    z : ndarray
        z coordinates
    mmax : int
        Maximum order to calculate to

    Returns
    -------

    modes : list of tuples
        a list of the (m, n) modes projected onto
    c : ndarray
        the mode coefficients


    """

    azimuth, zenith, r = coordinate_tools.cartesian_to_spherical(x, y, z)

    A = []
    modes = []
    for m in range(mmax + 1):
        for n in range(-m, m + 1):
            sp_mode = r_sph_harm(n, m, azimuth, zenith)
            A.append(sp_mode)

            modes.append((m, n))

    A = np.vstack(A).T

    tol = tol_init

    c = linalg.lstsq(A, r)[0]

    # recompute, discarding outliers
    for i in range(nIters):
        pred = np.dot(A, c)
        error = abs(r - pred) / r
        mask = error < tol
        # print mask.sum(), len(mask)

        c, summed_residuals, rank, singular_values = linalg.lstsq(A[mask, :], r[mask])
        tol /= 2

    return modes, c, summed_residuals


def reconstruct_from_modes(modes, coeffs, azimuth, zenith):
    r_ = 0

    for (m, n), c in zip(modes, coeffs):
        r_ += c * (r_sph_harm(n, m, azimuth, zenith))

    return r_


AXES = np.stack([[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]], axis=1)


def reconstruct_shell(modes, coeffs, azimuth, zenith):
    r = 0
    for (m, n), c in zip(modes, coeffs):
        r += c * (r_sph_harm(n, m, azimuth, zenith))

    return r


def scaled_shell_from_hdf(hdf_file, table_name='harmonic_shell'):
    """

    Parameters
    ----------
    hdf_file : str or tables.file.File
        path to hdf file or opened hdf file
    table_name : str
        name of the table containing the spherical harmonic expansion information

    Returns
    -------
    shell : ScaledShell
        see nucleus.spherical_harmonics.shell_tools.ScaledShell

    """
    from PYME.IO.MetaDataHandler import HDFMDHandler
    import tables
    try:
        opened_hdf_file = tables.open_file(hdf_file, 'r')
    except TypeError:
        opened_hdf_file = hdf_file
    shell = ScaledShell()
    shell.mdh = HDFMDHandler(opened_hdf_file)

    shell.standard_deviations = np.asarray(shell.mdh['spherical_harmonic_shell.standard_deviations'])
    shell.scaling_factors = np.asarray(shell.mdh['spherical_harmonic_shell.scaling_factors'])
    shell.principal_axes = np.asarray(shell.mdh['spherical_harmonic_shell.principal_axes'])

    shell.x0 = shell.mdh['spherical_harmonic_shell.x0']
    shell.y0 = shell.mdh['spherical_harmonic_shell.y0']
    shell.z0 = shell.mdh['spherical_harmonic_shell.z0']

    shell._summed_residuals = shell.mdh['spherical_harmonic_shell.summed_residuals']
    shell.sampling_fraction = shell.mdh['spherical_harmonic_shell.sampling_fraction']

    shell_table = getattr(getattr(opened_hdf_file, 'root'), table_name)
    shell._set_coefficients(shell_table[:]['modes'], shell_table[:]['coefficients'])

    return shell


class ScaledShell(object):
    data_type = [
        ('modes', '<2i4'),
        ('coefficients', '<f4'),
    ]

    def __init__(self, sampling_fraction=0.5):
        self.sampling_fraction = sampling_fraction

        self.modes = None
        self.coefficients = None

        self.x, self.y, self.z, = None, None, None
        self.x0, self.y0, self.z0 = None, None, None
        self.x_c, self.y_c, self.z_c, = None, None, None
        # note that all scalings will be centered
        self.x_cs, self.y_cs, self.z_cs, = None, None, None

        self.standard_deviations, self.principal_axes = None, None
        self.scaling_factors = None

    def to_recarray(self, keys=None):
        """

        Pretend we are a PYME.IO.tabular type

        Parameters
        ----------
        keys : None
            Ignored for this contrived function

        Returns
        -------
        numpy recarray version of self

        """
        record = np.recarray(len(self.coefficients), dtype=self.data_type)
        record['modes'] = self.modes
        record['coefficients'] = self.coefficients
        return record

    def to_hdf(self, filename, tablename='Data', keys=None, metadata=None):
        from PYME.IO import h5rFile, MetaDataHandler
        # NOTE that we ignore metadata input
        metadata = MetaDataHandler.NestedClassMDHandler()
        metadata['spherical_harmonic_shell.standard_deviations'] = self.standard_deviations.tolist()
        metadata['spherical_harmonic_shell.scaling_factors'] = self.scaling_factors.tolist()
        metadata['spherical_harmonic_shell.principal_axes'] = self.principal_axes.tolist()
        metadata['spherical_harmonic_shell.summed_residuals'] = self._summed_residuals
        metadata['spherical_harmonic_shell.n_points_used_in_fitting'] = len(self.x)
        metadata['spherical_harmonic_shell.x0'] = self.x0
        metadata['spherical_harmonic_shell.y0'] = self.y0
        metadata['spherical_harmonic_shell.z0'] = self.z0
        metadata['spherical_harmonic_shell.sampling_fraction'] = self.sampling_fraction

        with h5rFile.H5RFile(filename, 'a') as f:
            f.appendToTable(tablename, self.to_recarray(keys))
            f.updateMetadata(metadata)

    def _set_coefficients(self, modes, coefficients):
        assert len(modes) == len(coefficients)
        self.modes = modes
        self.coefficients = coefficients

    def set_fitting_points(self, x, y, z):
        assert (x.shape == y.shape) and (y.shape == z.shape)
        self.x, self.y, self.z = np.copy(x), np.copy(y), np.copy(z)
        self.x0, self.y0, self.z0 = self.x.mean(), self.y.mean(), self.z.mean()

        self.x_c, self.y_c, self.z_c = self.x - self.x0, self.y - self.y0, self.z - self.z0

        self._scale_fitting_points()

    def _scale_fitting_points(self):
        self.standard_deviations, self.principal_axes = coordinate_tools.find_principal_axes(self.x_c, self.y_c, self.z_c,
                                                                                             sample_fraction=self.sampling_fraction)
        self.scaling_factors = np.max(self.standard_deviations) / (self.standard_deviations)
        self.x_cs, self.y_cs, self.z_cs, = coordinate_tools.scaled_projection(self.x_c, self.y_c, self.z_c,
                                                                              self.scaling_factors, self.principal_axes)

    def get_fitted_shell(self, azimuth, zenith):
        r_scaled = reconstruct_shell(self.modes, self.coefficients, azimuth, zenith)
        x_scaled, y_scaled, z_scaled = coordinate_tools.spherical_to_cartesian(azimuth, zenith, r_scaled)
        # need to scale things "down" since they were scaled "up" in the fit
        # scaling_factors = 1. / self.scaling_factors

        scaled_axes = self.principal_axes / self.scaling_factors[:, None]

        coords = x_scaled.ravel()[:, None] * scaled_axes[0, :] + y_scaled.ravel()[:, None] * scaled_axes[1,
                                                                                             :] + z_scaled.ravel()[:,
                                                                                                  None] * scaled_axes[2,
                                                                                                          :]
        x, y, z = coords.T
        # x, y, z = vector_tools.scaled_projection(x_scaled, y_scaled, z_scaled, scaling_factors, self.principal_axes)

        return x.reshape(x_scaled.shape) + self.x0, y.reshape(y_scaled.shape) + self.y0, z.reshape(
            z_scaled.shape) + self.z0

    # def get_fitted_shell_cartesian(self, x, y, z):
    #     r_in, azimuth_in, zenith_in = vector_tools.cartesian_to_spherical(x, y, z)
    #     azi, zen, r = self.get_fitted_shell_spherical(azimuth_in, zenith_in)
    #     return vector_tools.spherical_to_cartesian(azi, zen, r)

    def fit_shell(self, max_m_mode=3, n_iterations=2, tol_init=0.3):
        modes, coefficients, summed_residuals = sphere_expansion_clean(self.x_cs, self.y_cs, self.z_cs, max_m_mode,
                                                                       n_iterations,
                                                                       tol_init)
        self._set_coefficients(modes, coefficients)
        self._summed_residuals = summed_residuals

    def check_inside(self, x=None, y=None, z=None):
        if x is None:
            xcs, ycs, zcs = self.x_cs, self.y_cs, self.z_cs
        else:
            xcs, ycs, zcs = coordinate_tools.scaled_projection(x - self.x0, y - self.y0, z - self.z0, self.scaling_factors,
                                                               self.principal_axes)

        azimuth, zenith, rcs = coordinate_tools.cartesian_to_spherical(xcs, ycs, zcs)
        r_cs_shell = reconstruct_shell(self.modes, self.coefficients, azimuth, zenith)
        return rcs < r_cs_shell

    def _visualize_shell(self, d_zenith=0.1, points=None):
        try:
            from mayavi import mlab
        except(ImportError):
            raise ImportError('Could not import mayavi.mlab.\
             Please make sure mayavi is installed to display fitted shell')

        if not points:
            x, y, z = self.x, self.y, self.z
        else:
            x, y, z = points
        zenith, azimuth = np.mgrid[0:(np.pi + d_zenith):d_zenith, 0:(2 * np.pi + d_zenith):d_zenith]

        xs, ys, zs = self.get_fitted_shell(azimuth, zenith)

        mlab.figure()
        mlab.mesh(xs, ys, zs)
        mlab.points3d(x, y, z, mode='point')

    # def _visualize_scaled(self):
    #     from mayavi import mlab
    #     visualize_shell(self.modes, self.coefficients)#, scaling_factors=self.standard_deviations,
    #                     # scaling_axes=self.principal_axes)
    #     mlab.points3d(self.x_cs, self.y_cs, self.z_cs, mode='point')

    # def _visualize(self):
    #     from mayavi import mlab
    #     visualize_shell(self.modes, self.coefficients, scaling_factors=1./self.scaling_factors,
    #                     scaling_axes=self.principal_axes)
    #     mlab.points3d(self.x_c, self.y_c, self.z_c, mode='point')

    def distance_to_shell(self, query_points, d_zenith=0.1,
                          return_inside_bool=False):  # FIXME - is return inside bool OK to remove now?
        """

        Parameters
        ----------
        query_points : list-like of ndarrays
            Arrays of positions to query (in cartesian coordinates), i.e. [np.array(x), np.array(y), np.array(z)]
        d_zenith : float
            Sets the step size in radians of zenith and azimuth arrays used in reconstructing the spherical harmonic shell

        Returns
        -------
        min_distance : float
            minimum distance from query points (i.e. input coordinate) to the spherical harmonic surface
        closest_points_on_surface : tuple of floats
            returns the position in cartesian coordinates of the point on the surface closest to the input 'position'

        """
        x, y, z = query_points
        x, y, z = np.atleast_1d(x), np.atleast_1d(y), np.atleast_1d(z)
        n_points = len(x)
        zenith, azimuth = np.mgrid[0:(np.pi + d_zenith):d_zenith, 0:(2 * np.pi + d_zenith):d_zenith]

        x_shell, y_shell, z_shell = self.get_fitted_shell(azimuth, zenith)
        # calculate the distance between all our points and the shell
        dist = np.sqrt(
            (x - x_shell[:, :, None]) ** 2 + (y - y_shell[:, :, None]) ** 2 + ((z - z_shell[:, :, None]) ** 2))

        # unfortunately cannot currently specify two axes for numpy.argmin, so we'll have to flatten the first two dims
        n_shell_coords = dist.shape[0] * dist.shape[1]
        dist_flat = dist.reshape((n_shell_coords, n_points))
        min_ind = np.argmin(dist_flat, axis=0)

        p_ind = range(n_points)
        return dist_flat[min_ind[p_ind], p_ind], (x_shell.reshape(n_shell_coords)[min_ind],
                                                  y_shell.reshape(n_shell_coords)[min_ind],
                                                  z_shell.reshape(n_shell_coords)[min_ind])

    def approximate_normal(self, x, y, z, d_azimuth=1e-6, d_zenith=1e-6, return_orthogonal_vectors=False):
        """

        Numerically approximate a vector(s) normal to the spherical harmonic shell at the query point(s).

        For input point(s), scale and convert to spherical coordinates, shift by +/- d_azimuth and d_zenith to get
        'phantom' points in the plane tangent to the spherical harmonic expansion on either side of the query point(s).
        Scale back, convert to cartesian, make vectors from the phantom points (which are by definition not parallel)
        and cross them to get a vector perpindicular to the plane.

        Returns
        -------

        Parameters
        ----------
        x : ndarray, float
            cartesian x location of point(s) on the surface to calculate the normal at
        y : ndarray, float
            cartesian y location of point(s) on the surface to calculate the normal at
        z : ndarray, float
            cartesian z location of point(s) on the surface to calculate the normal at
        d_azimuth : float
            azimuth step size for generating vector in plane of the shell [radians]
        d_zenith : float
            zenith step size for generating vector in plane of the shell [radians]

        Returns
        -------
        normal_vector : ndarray
            cartesian unit vector(s) normal to query point(s). size (len(x), 3)
        orth0 : ndarray
            cartesian unit vector(s) in the plane of the spherical harmonic shell at the query point(s), and
            perpendicular to normal_vector
        orth1 : ndarray
            cartesian unit vector(s) orthogonal to normal_vector and orth0

        """
        # scale the query points and convert them to spherical
        x_qs, y_qs, z_qs = coordinate_tools.scaled_projection(np.atleast_1d(x - self.x0), np.atleast_1d(y - self.y0),
                                                              np.atleast_1d(z - self.z0), self.scaling_factors,
                                                              self.principal_axes)
        azimuth, zenith, r = coordinate_tools.cartesian_to_spherical(x_qs, y_qs, z_qs)

        # get scaled shell radius at +/- points for azimuthal and zenith shifts
        azimuths = np.array([azimuth - d_azimuth, azimuth + d_azimuth, azimuth, azimuth])
        zeniths = np.array([zenith, zenith, zenith - d_zenith, zenith + d_zenith])
        r_scaled = reconstruct_shell(self.modes, self.coefficients, azimuths, zeniths)

        # convert shifted points to cartesian and scale back. shape = (4, #points)
        x_scaled, y_scaled, z_scaled = coordinate_tools.spherical_to_cartesian(azimuths, zeniths, r_scaled)
        # scale things "down" since they were scaled "up" in the fit
        scaled_axes = self.principal_axes / self.scaling_factors[:, None]
        coords = x_scaled.ravel()[:, None] * scaled_axes[0, :] + y_scaled.ravel()[:, None] * scaled_axes[1,
                                                                                             :] + z_scaled.ravel()[:,
                                                                                                  None] * scaled_axes[2,
                                                                                                          :]
        x_p, y_p, z_p = coords.T
        # skip adding x0, y0, z0 back on, since we'll subtract it off in a second
        x_p, y_p, z_p = x_p.reshape(x_scaled.shape), y_p.reshape(y_scaled.shape), z_p.reshape(z_scaled.shape)

        # make two vectors in the plane centered at the query point
        v0 = np.array([x_p[1] - x_p[0], y_p[1] - y_p[0], z_p[1] - z_p[0]])
        v1 = np.array([x_p[3] - x_p[2], y_p[3] - y_p[2], z_p[3] - z_p[2]])
        # cross them to get a normal vector NOTE - direction could be negative of true normal
        normal = np.cross(v0, v1, axis=0)
        # return as unit vector(s) along each row
        normal = np.atleast_2d(normal / np.linalg.norm(normal, axis=0)).T
        # make sure normals point outwards, by dotting it with the vector to the point on the shell from the center
        points = np.stack([np.atleast_1d(x - self.x0), np.atleast_1d(y - self.y0), np.atleast_1d(z - self.z0)]).T
        outwards = np.array([np.dot(normal[ind], points[ind]) > 0 for ind in range(normal.shape[0])])
        normal[~outwards, :] *= -1
        if np.isnan(normal).any():
            raise RuntimeError('Failed to calculate normal vector')
        if return_orthogonal_vectors:
            orth0 = np.atleast_2d(v0 / np.linalg.norm(v0, axis=0)).T
            # v0 and v1 are both in a plane perpendicular to normal, but not strictly orthogonal to each other
            orth1 = np.cross(normal, orth0, axis=1)  # replace v1 with a unit vector orth. to both normal and v0
            return normal.squeeze(), orth0.squeeze(), orth1.squeeze()
        return normal.squeeze()

    def _distance_error(self, parameterized_distance, vector, starting_point):
        """

        Calculate the error in scaled space between the shell and the point reached traveling a specified distance(s)
        along the input vector from the input starting position.

        This function is to be minimized by a solver. Note that we don't actually have to calculate the distance in
        normal space, since minimizing in the scale space is equivalent.

        Parameters
        ----------
        parameterized_distance : float
            distance along vector to travel, units of nm, unscaled
        vector : ndarray
            Length three, vector in (unscaled) cartesian coordinates along which to travel
        starting_point : ndarray or list
            Length three, point in (unscaled) cartesian space from which to start traveling along 'vector'

        Returns
        -------

        """
        x, y, z = [parameterized_distance * np.atleast_2d(vector)[:, ind] + np.atleast_2d(starting_point)[:, ind] for
                   ind in range(3)]
        # scale the query points and convert them to spherical
        x_qs, y_qs, z_qs = coordinate_tools.scaled_projection(np.atleast_1d(x - self.x0), np.atleast_1d(y - self.y0),
                                                              np.atleast_1d(z - self.z0), self.scaling_factors,
                                                              self.principal_axes)
        azimuth_qs, zenith_qs, r_qs = coordinate_tools.cartesian_to_spherical(x_qs, y_qs, z_qs)

        # get scaled shell radius at those angles
        r_shell = reconstruct_shell(self.modes, self.coefficients, azimuth_qs, zenith_qs)

        # return the (scaled space) difference
        return r_qs - r_shell

    def distance_to_shell_along_vector_from_point(self, vector, starting_point, guess=None):
        """

        Calculate the distance to the shell along a given direction, from a given point.

        Parameters
        ----------
        vector : list-like
            cartesian vector indicating direction to query for proximity to shell
        starting_point : list-like
            cartesian position from which to start traveling along input vector when calculating shell proximity
        guess_distances : array, float
            initial guess for distance solver. See self._distance_error()

        Returns
        -------

        """

        if guess is None:
            guess = self._find_guess_for_distance_to_shell_along_vector_from_point(vector, starting_point)

        (res, cov_x, info_dict, mesg, res_code) = optimize.leastsq(self._distance_error, guess,
                                                                   args=(vector, starting_point),
                                                                   full_output=1)
        return res

    def _find_guess_for_distance_to_shell_along_vector_from_point(self, vector, starting_point, guess_distances=None):
        """

        Calculate the distance to the shell along a given direction, from a given point.

        Parameters
        ----------
        vector : list-like
            cartesian vector indicating direction to query for proximity to shell
        starting_point : list-like
            cartesian position from which to start traveling along input vector when calculating shell proximity
        guess_distances : array, float
            initial guess for distance solver. See self._distance_error()

        Returns
        -------

        """
        guess_distances = np.arange(0., 1000., 100.) if guess_distances is None else guess_distances
        errors = np.zeros_like(guess_distances)
        # guess = guess_distances[np.argmin(np.abs(self._distance_error(guess_distances, starting_point, vector)))]
        for ind, query in enumerate(guess_distances):
            errors[ind] = self._distance_error(query, vector, starting_point)

        return guess_distances[np.argmin(np.abs(errors))]

