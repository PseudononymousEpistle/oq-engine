/*
    Copyright (c) 2010-2012, GEM Foundation.

    OpenQuake is free software: you can redistribute it and/or modify it
    under the terms of the GNU Affero General Public License as published
    by the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    OpenQuake is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU Affero General Public License
    along with OpenQuake.  If not, see <http://www.gnu.org/licenses/>.
*/

package org.gem.calc;

public class UHSResult
{
    private double poe;
    private Double[] uhs;

    public UHSResult(double poe, Double[] uhs)
    {
        this.poe = poe;
        this.uhs = uhs;
    }

    public double getPoe()
    {
        return poe;
    }

    public Double[] getUhs()
    {
        return uhs;
    }
}
