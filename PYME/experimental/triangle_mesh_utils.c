// #include <stdio.h>
// #include <stdlib.h>
#include "Python.h"
#include <math.h>
#include "numpy/arrayobject.h"

#include "triangle_mesh_utils.h"

/*

DB: Performance suggestions

There is potentially scope to do some type hinting here to get the compiler to generate SMID instructions (e.g. by using vector types)

This may or may not be worthwhile, as the code will likely be compiler specific / different for gcc / msvc. Also the compiler
should be fairly good at doing it for you.

The one thing you can definitely do is flag the variables that are unchanged during the function call as const ... e.g.

float norm(const float * const pos)

Which gives the compiler more info and is definitely not going to generate slower code.

You could potentially also flag the functions as inline, but I'm not sure how much good that will do on modern compilers,
they should do that for you anyway.

I'm not sure if there is an easy way of doing it, but as you divide by the norm, getting the compiler to generate
rsqrt(n) and then multiplying by that could be significantly faster (I think there are more pressing things to address).

*/
float norm(float *pos)
{
    int i;
    float n = 0;
    for (i = 0; i < VECTORSIZE; ++i)
        n += pos[i] * pos[i];
    return sqrt(n);
}

void cross(float *a, float *b, float *n)
{
    /* DB: I think the compiler should be able to optimize this away, but I'd probably not copy the array items into
    separate variables first, but rather just have n[0] = a[1]*b[2] - a[2]*b[1] etc ... A naive compiler would generate
    slower code for how you've written it.

    One thing that might be worth thinking about is making a typedef for a vector - As it stands, the compiler doesn't
    have any info about the overall dimensions of a, b, or n, or their alignment in memory but I'm not sure that matters.

    Once again, I think there is more mileage to be had by moving more of the mesh class to c/cython first.
    */
    float a0, a1, a2, b0, b1, b2;

    a0 = a[0]; a1 = a[1]; a2 = a[2];
    b0 = b[0]; b1 = b[1]; b2 = b[2];

    n[0] = a1*b2 - a2*b1;
    n[1] = a2*b0 - a0*b2;
    n[2] = a0*b1 - a1*b0;
}

// void update_vertex_neighbors(signed int *v_idxs, halfedge_t *halfedges, vertex_t *vertices, face_t *faces, signed int n_idxs)

/*
DB: Performance suggestions:

- change to a pure c function and a python c api wrapper
  reasons are 1 : facilitates moving more of the code to c 2: forces us to code in a way that doesn't make python API
  calls within loop. This is good both because it will result in better performance, and also in that it will (potentially)
  let us release the GIL and get performance improvements from multithreading

- PySequence_GetItem() is a python API call, we probably don't want it in the loop if we can help it.

- I'm pretty sure that PyArray_GETPTR1 is a macro, which makes it significantly less costly (expands to inline c code, &
  no need for GIL). That said, we know that the input arrays are contiguous (we check for this) so we could also just have,
  e.g., vertex_t * p_vertices = PyArray_GETPTR1(vertices, 0) outside the loop and then just replace everything inside
  the loop with standard c indexing - ie curr_vertex = p_vertices[v_idx]. My strong suspicion is that the c compiler will do a
  better job of optimizing this, and it will also continue to work once more of the code is in c.

*/
static PyObject *update_vertex_neighbors(PyObject *self, PyObject *args)
{
    PyObject *v_idxs=0, *halfedges=0, *vertices=0, *faces=0;
    signed int i, j, k, v_idx, orig_idx, curr_idx, twin_idx, n_idxs, tmp;
    halfedge_t *curr_edge, *twin_edge;
    vertex_t *curr_vertex, *loop_vertex;
    face_t *curr_face;

    float position[VECTORSIZE];
    float normal[VECTORSIZE];
    float a, l, nn;

    if (!PyArg_ParseTuple(args, "OOOO", &v_idxs, &halfedges, &vertices, &faces)) return NULL;
    if (!PySequence_Check(v_idxs))
    {
        PyErr_Format(PyExc_RuntimeError, "expecting an sequence  eg ... (xvals, yvals) or (xvals, yvals, zvals)");
        return NULL;
    }
    if (!PyArray_Check(halfedges) || !PyArray_ISCONTIGUOUS(halfedges))
    {
        PyErr_Format(PyExc_RuntimeError, "Expecting a contiguous numpy array for the edge data.");
        return NULL;
    }
    if (!PyArray_Check(vertices) || !PyArray_ISCONTIGUOUS(vertices)) 
    {
        PyErr_Format(PyExc_RuntimeError, "Expecting a contiguous numpy array for the vertex data.");
        return NULL;
    }
    if (!PyArray_Check(faces) || !PyArray_ISCONTIGUOUS(faces)) 
    {
        PyErr_Format(PyExc_RuntimeError, "Expecting a contiguous numpy array for the face data.");
        return NULL;
    } 

    n_idxs = (signed int)PySequence_Length(v_idxs);

    for (j = 0; j < n_idxs; ++j)
    {
        v_idx = (signed int)PyArray_DATA(PySequence_GetItem(v_idxs, (Py_ssize_t) j));
        if (v_idx == -1)
            continue;
        curr_vertex = (vertex_t*)PyArray_GETPTR1(vertices, v_idx);

        orig_idx = curr_vertex->halfedge;
        curr_idx = orig_idx;

        if (curr_idx == -1) continue;

        curr_edge = (halfedge_t*)PyArray_GETPTR1(halfedges, curr_idx);
        twin_idx = curr_edge->twin;
        if (twin_idx != -1)
            twin_edge = (halfedge_t*)PyArray_GETPTR1(halfedges, twin_idx);

        i = 0;

        for (k = 0; k < NEIGHBORSIZE; ++k)
            (curr_vertex->neighbors)[k] = -1;

        for (k = 0; k < VECTORSIZE; ++k)
            normal[k] = 0;

        while (1)
        {
            if (curr_idx == -1)
                break;

            if (i < NEIGHBORSIZE)
            {
                (curr_vertex->neighbors)[i] = curr_idx;
                curr_face = (face_t*)PyArray_GETPTR1(faces, (curr_edge->face));
                a = curr_face->area;
                for (k = 0; k < VECTORSIZE; ++k) 
                    normal[k] += ((curr_face->normal)[k])*a;
            }

            loop_vertex = (vertex_t*)PyArray_GETPTR1(vertices, (curr_edge->vertex));

            for (k = 0; k < VECTORSIZE; ++k) 
                position[k] = (curr_vertex->position)[k] - (loop_vertex->position)[k];

            l = norm(position);
            curr_edge->length = l;
            if (twin_idx == -1)
                break;
            twin_edge->length = l;

            curr_idx = twin_edge->next;
            curr_edge = (halfedge_t*)PyArray_GETPTR1(halfedges, curr_idx);
            twin_idx = curr_edge->twin;
            twin_edge = (halfedge_t*)PyArray_GETPTR1(halfedges, twin_idx);

            ++i;

            if (curr_idx == orig_idx)
                break;
        }

        // If we hit a boundary, try the reverse direction, 
        // starting from orig_idx
        // twin now becomes prev
        if ((twin_idx == -1) && (curr_idx != -1))
        {
            // Ideally we sweep through the neighbors in a continuous fashion,
            // so we'll reverse the order of all the edges so far.
            for (k=i;k>1;k--)
            {
                tmp = (curr_vertex->neighbors)[k];
                (curr_vertex->neighbors)[k] = (curr_vertex->neighbors)[k-1];
                (curr_vertex->neighbors)[k-1] = tmp;
            }

            curr_idx = orig_idx;
            curr_edge = (halfedge_t*)PyArray_GETPTR1(halfedges, curr_idx);
            twin_idx = curr_edge->prev;
            if (twin_idx == -1)
                continue;
            twin_edge = (halfedge_t*)PyArray_GETPTR1(halfedges, twin_idx);
            curr_idx = twin_edge->twin;
            if (curr_idx == -1)
                continue;
            curr_edge = (halfedge_t*)PyArray_GETPTR1(halfedges, curr_idx);

            ++i;

            while (1)
            {
                if (curr_idx == -1)
                    break;

                if (i < NEIGHBORSIZE)
                {
                    (curr_vertex->neighbors)[i] = curr_idx;
                    curr_face = (face_t*)PyArray_GETPTR1(faces, (curr_edge->face));
                    a = curr_face->area;
                    for (k = 0; k < VECTORSIZE; ++k) 
                        normal[k] += ((curr_face->normal)[k])*a;
                }

                loop_vertex = (vertex_t*)PyArray_GETPTR1(vertices, (curr_edge->vertex));

                for (k = 0; k < VECTORSIZE; ++k) 
                    position[k] = (curr_vertex->position)[k] - (loop_vertex->position)[k];

                l = norm(position);
                curr_edge->length = l;
                twin_edge->length = l;

                twin_idx = curr_edge->prev;
                if (twin_idx == -1)
                    break;
                twin_edge = (halfedge_t*)PyArray_GETPTR1(halfedges, twin_idx);
                curr_idx = twin_edge->twin;
                if (curr_idx == -1)
                    break;
                curr_edge = (halfedge_t*)PyArray_GETPTR1(halfedges, curr_idx);

                if (curr_idx == orig_idx)
                    break;

                ++i;
            }
        }

        curr_vertex->valence = i;

        nn = norm(normal);
        if (nn > 0) {
            for (k = 0; k < VECTORSIZE; ++k)
                (curr_vertex->normal)[k] = normal[k]/nn;
        } else {
            for (k = 0; k < VECTORSIZE; ++k)
                (curr_vertex->normal)[k] = 0;
        }
    }

    // Python's garbage collection will Py_DECREF halfedges, vertices, faces after each function call, so insure against this
    Py_INCREF(halfedges);
    Py_INCREF(vertices);
    Py_INCREF(faces);
    Py_INCREF(Py_None);
    return Py_None;
}

// void update_face_normals(signed int *f_idxs, halfedge_t *halfedges, vertex_t *vertices, face_t *faces, signed int n_idxs)
static PyObject *update_face_normals(PyObject *self, PyObject *args)
{
    PyObject *f_idxs=0, *halfedges=0, *vertices=0, *faces=0;
    signed int j, k, f_idx, curr_idx, prev_idx, next_idx, n_idxs;
    float v1[VECTORSIZE], u[VECTORSIZE], v[VECTORSIZE], n[VECTORSIZE], nn;
    halfedge_t *curr_edge, *prev_edge, *next_edge;
    face_t *curr_face;
    vertex_t *curr_vertex, *prev_vertex, *next_vertex;

    if (!PyArg_ParseTuple(args, "OOOO", &f_idxs, &halfedges, &vertices, &faces)) return NULL;
    if (!PySequence_Check(f_idxs))
    {
        PyErr_Format(PyExc_RuntimeError, "expecting an sequence  eg ... (xvals, yvals) or (xvals, yvals, zvals)");
        return NULL;
    }
    if (!PyArray_Check(halfedges) || !PyArray_ISCONTIGUOUS(halfedges))
    {
        PyErr_Format(PyExc_RuntimeError, "Expecting a contiguous numpy array for the edge data.");
        return NULL;
    }
    if (!PyArray_Check(vertices) || !PyArray_ISCONTIGUOUS(vertices)) 
    {
        PyErr_Format(PyExc_RuntimeError, "Expecting a contiguous numpy array for the vertex data.");
        return NULL;
    }
    if (!PyArray_Check(faces) || !PyArray_ISCONTIGUOUS(faces)) 
    {
        PyErr_Format(PyExc_RuntimeError, "Expecting a contiguous numpy array for the face data.");
        return NULL;
    } 

    n_idxs = (signed int)PySequence_Length(f_idxs);

    for (j = 0; j < n_idxs; ++j)
    {
        f_idx = (signed int)PyArray_DATA(PySequence_GetItem(f_idxs, (Py_ssize_t) j));
        if (f_idx == -1)
            continue;
        curr_face = (face_t*)PyArray_GETPTR1(faces, f_idx);

        curr_idx = curr_face->halfedge;
        if (curr_idx == -1) continue;
        curr_edge = (halfedge_t*)PyArray_GETPTR1(halfedges, curr_idx);

        prev_idx = curr_edge->prev;
        if (prev_idx == -1) continue;
        prev_edge = (halfedge_t*)PyArray_GETPTR1(halfedges, prev_idx);

        next_idx = curr_edge->next;
        if (next_idx == -1) continue;
        next_edge = (halfedge_t*)PyArray_GETPTR1(halfedges, next_idx);

        curr_vertex = (vertex_t*)PyArray_GETPTR1(vertices, (curr_edge->vertex));
        prev_vertex = (vertex_t*)PyArray_GETPTR1(vertices, (prev_edge->vertex));
        next_vertex = (vertex_t*)PyArray_GETPTR1(vertices, (next_edge->vertex));

        for (k = 0; k < VECTORSIZE; ++k)
            v1[k] = (curr_vertex->position)[k];

        for (k = 0; k < VECTORSIZE; ++k)
        {
            u[k] = (prev_vertex->position)[k] - v1[k];
            v[k] = (next_vertex->position)[k] - v1[k];
        }

        cross(u, v, n);
        nn = norm(n);
        curr_face->area = 0.5*nn;

        if (nn > 0){
            for (k = 0; k < VECTORSIZE; ++k)
                (curr_face->normal)[k] = n[k]/nn;
        } else {
            for (k = 0; k < VECTORSIZE; ++k)
                (curr_face->normal)[k] = 0;
        }
    }

    // Python's garbage collection will Py_DECREF halfedges, vertices, faces after each function call, so insure against this
    Py_INCREF(halfedges);
    Py_INCREF(vertices);
    Py_INCREF(faces);
    Py_INCREF(Py_None);
    return Py_None;
}

static PyMethodDef triangle_mesh_utils_methods[] = {
    {"c_update_vertex_neighbors", update_vertex_neighbors, METH_VARARGS},
    {"c_update_face_normals", update_face_normals, METH_VARARGS},
    {NULL, NULL, 0}  /* Sentinel */
};

#if PY_MAJOR_VERSION >= 3
static struct PyModuleDef moduledef = {
        PyModuleDef_HEAD_INIT,
        "triangle_mesh_utils",     /* m_name */
        "C implementations of triangle_mesh operations for speed improvement",  /* m_doc */
        -1,                  /* m_size */
        triangle_mesh_utils_methods,    /* m_methods */
        NULL,                /* m_reload */
        NULL,                /* m_traverse */
        NULL,                /* m_clear */
        NULL,                /* m_free */
    };

PyMODINIT_FUNC PyInit_triangle_mesh_utils(void)
{
	PyObject *m;
    m = PyModule_Create(&moduledef);
    import_array()
    return m;
}
#else
PyMODINIT_FUNC inittriangle_mesh_utils(void)
{
    PyObject *m;

    m = Py_InitModule("triangle_mesh_utils", triangle_mesh_utils_methods);
    import_array()

}
#endif